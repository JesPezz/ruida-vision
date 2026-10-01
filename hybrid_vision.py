"""Vision hibrida para laser CO2 con controladora Ruida.

Cenital (coarse) -> marca de registro en mm via homografia.
Cabezal (fine)   -> microscopio: recentra la marca con submicro precision.

Flujo: calibrate -> run. Configuracion y homografia en calib.json.
"""

import argparse
import itertools
import json
import math
import os
import sys
import time

import cv2
import numpy as np

try:
    import msvcrt
except ImportError:      # fuera de Windows no hay consola que leer
    msvcrt = None

import ruida

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "calib.json")
BED_PNG = os.path.join(HERE, "bed.png")
MARKS_PNG = os.path.join(HERE, "marks.png")
# el orden importa: DSHOW es el que funciona con camaras USB en Windows
BACKENDS = [getattr(cv2, "CAP_DSHOW", -1), cv2.CAP_ANY]
DEFAULTS = {
    "ip": "192.168.1.50",
    "top_cam": 2,
    "head_cam": 0,
    "res": [1920, 1080],
    "top_res": [1920, 1080],
    "head_res": [1280, 720],
    "fps": 30,
    "park": [20.0, 20.0],
    "head_fov_mm": 30.0,
    "head_flip_x": 1,
    "head_flip_y": 1,
    "wasd_flip": 0,
    "cam_offset_mm": [0.0, 0.0],
    "marks": 2,
    "settle": 1.5,
    "min_area": 8,
    "max_area": 40000,
    "thr": 0,
    "exposure": None,
    "gain": None,
}


def load_cfg():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG):
        cfg.update(json.load(open(CFG)))
    return cfg


def save_cfg(cfg):
    json.dump(cfg, open(CFG, "w"), indent=2)
    print("guardado %s" % CFG)


# --------------------------------------------------------------------- camaras

def open_cam(cfg, which):
    idx = cfg["%s_cam" % which]
    # cada camara tiene su nativa: la cenital es mas grande que la del cabezal,
    # asi que pedirle la de la otra hace que el driver avise cada vez
    res = cfg.get("%s_res" % which) or cfg["res"]
    # El MJPG hay que PEDIRLO al abrir. Ponerlo despues con cap.set() no lo
    # negocia: el driver se queda en YUY2, que son 3,7 MB por fotograma a 1080p,
    # y la camara baja a 2 fps. Medido en esta maquina: abriendo con los
    # parametros, MJPG y 25 fps, y el primer fotograma a los 1,7 s; poniendo el
    # fourcc despues, YUY2, 2,2 fps y 8,4 s. El `set` de aqui abajo se queda
    # para la exposicion y el gain, que si aceptanirse en caliente.
    params = [cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"),
              cv2.CAP_PROP_FRAME_WIDTH, res[0],
              cv2.CAP_PROP_FRAME_HEIGHT, res[1],
              cv2.CAP_PROP_FPS, cfg["fps"]]
    cap = cv2.VideoCapture(idx, BACKENDS[0], params)
    if not cap.isOpened():
        cap.release()                      # ese camino no sirve: el de antes
        for be in BACKENDS:
            cap = cv2.VideoCapture(idx, be)
            if cap.isOpened():
                break
            cap.release()
    if not cap.isOpened():
        raise IOError("no se abre la camara %s (indice %d). Prueba otro indice "
                      "o revisa que el driver la vea" % (which, idx))
    if cfg["exposure"] is not None:
        cap.set(cv2.CAP_PROP_EXPOSURE, cfg["exposure"])
    if cfg["gain"] is not None:
        cap.set(cv2.CAP_PROP_GAIN, cfg["gain"])
    for _ in range(12):
        cap.read()
    # el driver puede no dar lo pedido: la real es la unica que vale
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if (w, h) != tuple(res):
        print("AVISO: %s pedia %dx%d y entrega %dx%d; se usa la real"
              % (which, res[0], res[1], w, h))
    print("camara %s: indice %d, %dx%d" % (which, idx, w, h))
    return cap


def grab(cap, n=3):
    """Gris y nitido: de n fotogramas devuelve el mas enfocado."""
    best, score = None, -1.0
    for _ in range(n):
        ok, f = cap.read()
        if not ok:
            raise IOError("fallo de captura de video")
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        s = cv2.Laplacian(g, cv2.CV_64F).var()
        if s > score:
            best, score = g, s
    return best


def read_gris(cap):
    """Un fotograma en gris, sin Laplacian y sin elegir el mas enfocado.

    Para lo que se esta VIENDO en vivo. El mas enfocado importa cuando hay que
    APUNTAR a algo (un clic de calibracion, el centrado fino), no mientras se
    navega con el teclado: el ojo perdona algo de borroso. Con tres fotogramas y
    un Laplaciano CV_64F por camara y por vuelta, a 1920x1080 son 16 MB de ida y
    vuelta por fotograma, seis por vuelta, y eso es el lag y el cuelgue.
    """
    ok, f = cap.read()
    if not ok:
        raise IOError("fallo de captura de video")
    return cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- deteccion

def find_marks(gray, roi=None, min_area=8, max_area=40000, thr=0, merge=1,
               show=False, min_circularity=0.0, max_aspect_ratio=float("inf")):
    """Marcas de registro: componentes oscuros de area plausible, centro
    subpixel por momentos. `min_circularity` permite excluir textura, reflejos
    y bordes cuando la vista solo debe resaltar marcas redondas.

    `merge` agrupa en una sola marca las detecciones cuyos centroides caen en el
    mismo pixel. El tag de LightBurn es un aro con la cruz dentro, y sin esto son
    dos blobs: dos detecciones y dos marcas fantasma. Como el centroide de un aro
    y el de una cruz concentricos caen ambos en el centro del tag, agrupar por
    centroide las junta sin depender de la separacion entre aro y cruz, que
    cambia con el tamano. Los tags sueltos de la cama van tan lejos que no se
    tocan."""
    x0, y0 = (roi[0], roi[1]) if roi else (0, 0)
    g = gray[y0:roi[3], x0:roi[2]] if roi else gray
    if thr:
        binimg = (g < thr).astype(np.uint8)
    else:
        binimg = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1] // 255
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binimg, 8)
    out = []
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a < min_area or a > max_area:
            continue
        x, y, w, h = stats[i, :4]
        aspect_ratio = max(w, h) / max(1, min(w, h))
        if aspect_ratio > max_aspect_ratio:
            continue
        component = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        if min_circularity:
            contours, _ = cv2.findContours(
                component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            circularity = 4.0 * math.pi * cv2.contourArea(contour) / perimeter ** 2
            if circularity < min_circularity:
                continue
        m = cv2.moments(component)
        if m["m00"] <= 0:
            continue
        out.append((x0 + x + m["m10"] / m["m00"],
                    y0 + y + m["m01"] / m["m00"], int(a)))
    out.sort(key=lambda p: p[2], reverse=True)
    if merge:
        # De mayor a menor area: cada deteccion se une a la primera del grupo
        # que ya tenga el centro a menos de `merge` pixeles, y el centro del
        # grupo es la media ponderada por area.
        grupos = []
        for u, v, a in out:
            for j, (U, V, A) in enumerate(grupos):
                if abs(U - u) <= merge and abs(V - v) <= merge:
                    t = A + a
                    grupos[j] = ((U * A + u * a) / t, (V * A + v * a) / t, t)
                    break
            else:
                grupos.append((u, v, a))
        out = sorted(grupos, key=lambda p: p[2], reverse=True)
    if show:
        vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        for u, v, a in out:
            cv2.drawMarker(vis, (int(u), int(v)), (0, 0, 255),
                           cv2.MARKER_CROSS, 20, 1)
        cv2.imwrite(MARKS_PNG, vis)
        print("marcas: %s" % MARKS_PNG)
    return out


def click_point(gray, prompt, n=1):
    pts = []
    win = "click"
    cv2.namedWindow(win)
    cv2.imshow(win, gray)
    cv2.setMouseCallback(win, lambda e, x, y, f, _=None:
                         pts.append((x, y)) if e == cv2.EVENT_LBUTTONDOWN else None)
    print(prompt + "  (clic para marcar, q para cancelar)")
    while len(pts) < n:
        if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
            cv2.destroyWindow(win)
            raise KeyboardInterrupt("cancelado por el usuario")
    cv2.destroyWindow(win)
    return pts[0] if n == 1 else pts[:n]


# --------------------------------------------------------------- homografia

def fit_homography(px, mm):
    """Devuelve (H, error_max, error_medio, descartados).

    Los errores se miden SOLO sobre los puntos que RANSAC acepta. Medirlos
    tambien sobre los descartados es lo que hacia que una calibracion buena
    (5 puntos a 0.8 mm) pareciese un desastre (477 mm) y obligaba a repetir
    trabajo ya hecho. Los descartados se devuelven aparte, con su numero, para
    poder decir cuales son sin tener que adivinarlos.
    """
    src = np.array(px, np.float32)
    dst = np.array(mm, np.float32)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if H is None:                      # no hay modelo: sin inliers, se avisa
        return None, float("inf"), float("inf"), list(range(len(px)))
    mask = mask.ravel().astype(bool)
    kept = [i for i in range(len(px)) if mask[i]]
    if len(kept) < 4:                 # demasiado pocos puntos buenos
        return None, float("inf"), float("inf"), list(range(len(px)))
    err = {i: math.dist(tuple(p), dst[i]) for i, p in
           zip(kept, cv2.perspectiveTransform(src[kept][:, None, :], H)[:, 0, :])}
    return (H, max(err.values()), sum(err.values()) / len(err),
            [i for i in range(len(px)) if not mask[i]])


def px_to_mm(H, px):
    p = cv2.perspectiveTransform(np.array([px], np.float32)[:, None, :], H)
    return float(p[0, 0, 0]), float(p[0, 0, 1])


# ------------------------------------------------------------------ maquina

class Machine:
    def __init__(self, cfg, need_pos=True):
        self.cfg = cfg
        self.pan = ruida.Panel(cfg["ip"]) if need_pos else None
        self.move_link = None
        # Si la Ruida no contesta hay que decirlo EN PANTALLA, no solo por
        # consola: en la ventana no hay consola y el aviso se perdia.
        self.ok = bool(self.pan and self.pan.handshake())
        if not self.ok:
            print("AVISO: la Ruida no contesta en 50207; se usara espera fija")

    def park(self):
        return self.goto(*self.cfg["park"])

    def goto(self, x, y):
        raise RuntimeError(
            "movimiento automatico de workflow suspendido; usa Mover 1/2 "
            "en Print and Cut para el viaje nativo de prueba")

    def goto_native(self, x, y):
        if not ruida.NATIVE_POSITION_MOVE_ENABLED:
            raise RuntimeError("el movimiento nativo a coordenadas esta deshabilitado")
        if self.pan is None:
            raise RuntimeError("sin conexion al panel 50207 para confirmar la posicion")
        if self.move_link is None:
            self.move_link = ruida.Ruida(
                self.cfg["ip"], magic=self.cfg.get("magic", ruida.MAGIC))
        return ruida.move_and_wait(self.pan, self.move_link, x, y)

    def pos(self, timeout=1.0):
        if not self.pan:
            return None
        return self.pan.position(timeout)

    def close(self):
        for c in (self.pan, self.move_link):
            if c:
                c.close()


def ask_position():
    x, y = input("X Y en mm (los que pone LightBurn): ").split()
    return float(x), float(y)


# ------------------------------------------------------------------ fases

# FLECHAS DIRECCIONALES DEL TECLADO. El codigo VK va en la palabra alta
# (VK_LEFT=0x25 -> 0x250000 = 2424832, el valor de todos los ejemplos de
# OpenCV), pero como lo empaqueta depende de la version de OpenCV y del
# teclado, y aqui se aceptan todas las formas conocidas:
#   0x250000        VK en la palabra alta, lo normal
#   0x25004B        VK arriba con el scan code pegado debajo
#   0xFFFFFFE0      y al siguiente waitKey el scan code (ARROW_EXT)
#   0x25 a secas    el VK suelto
#   0x21 a 0x24     el teclado numerico con NumLock apagado, que produce
#                   flechas pero como Home/End/Pagina arriba/Pagina abajo
# Abajo es -Y porque el origen de la maquina esta abajo a la izquierda.
VK = {0x25: "-X", 0x26: "+Y", 0x27: "+X", 0x28: "-Y"}
SCAN = {0x48: "+Y", 0x4B: "-X", 0x4D: "+X", 0x50: "-Y"}
NUMPAD = {0x21: "+Y", 0x22: "-X", 0x23: "+X", 0x24: "-Y"}
ARROW_EXT = -32          # 0xFFFFFFE0 con signo, precede al scan code
# Los scan codes de msvcrt son OTROS que los de OpenCV: aqui la izquierda es P
# y abajo S, mientras que en OpenCV la izquierda es K y abajo P. No mezclarlos.
MS_SCAN = {0x48: "+Y", 0x50: "-X", 0x4D: "+X", 0x53: "-Y"}
MS_NUMPAD = {0x48: "+Y", 0x4B: "-X", 0x4D: "+X", 0x47: "-Y"}   # con NumLock off

# LETRAS. Las flechas aqui llegan partidas: en la consola se lee el prefijo
# '\xe0' y el segundo byte no aparece nunca, y en la ventana de OpenCV el
# codigo depende de como se empaquete. Una letra llega siempre y en un solo
# golpe, que es lo que hace falta. WASD, que es lo que se prueba primero.
# La velocidad va en 'v' porque 's' aqui es abajo y no se puede pisar.
# Mayusculas tambien, por si el Bloq Mayus esta puesto.
LETRAS = {"w": "+Y", "a": "-X", "s": "-Y", "d": "+X"}
VEL_KEY = "v"

# Que letra va a que eje NO se escribe a mano: sale de la homografia. El origen
# de la maquina esta en la esquina inferior izquierda, pero eso no dice nada de
# como esta montada la cenital: si la camara va girada 180 grados, la Y de la
# maquina cae hacia abajo en la imagen y "arriba" en pantalla es -Y. Con W a +Y
# el cabezal se va justo al reves de lo que espera el ojo (que es lo que paso:
# no decia "un eje va mal", decia "va a la inversa", o sea los dos).


def _op(d):
    """El eje contrario: '+X' -> '-X'."""
    return ("-" if d[0] == "+" else "+") + d[1]


def _wasd_de_H(H, w, h, paso=50):
    """Mapeo de WASD deducido de la homografia, no de los ejes de la maquina.

    Se pregunta a H por donde caen "derecha" y "arriba" EN LA IMAGEN (en una
    imagen py crece hacia abajo, por eso arriba es restar) y se toma el signo.
    Asi el mapeo sale siempre de lo que se ve en la cenital, y no hay que volver
    a tocarlo si un dia se gira o se espeja la camara.
    """
    p0 = px_to_mm(H, (w / 2.0, h / 2.0))
    der = px_to_mm(H, (w / 2.0 + paso, h / 2.0))     # derecha en la imagen
    arr = px_to_mm(H, (w / 2.0, h / 2.0 - paso))     # arriba en la imagen
    ex = "+X" if der[0] >= p0[0] else "-X"
    ey = "+Y" if arr[1] >= p0[1] else "-Y"
    return {"d": ex, "a": _op(ex), "w": ey, "s": _op(ey)}


def _mapa_wasd(cfg, cal, flip=0):
    """El mapeo que se usa de verdad, con el volteo manual encima.

    Sin homografia todavia (la primera calibracion es justo cuando hace falta)
    se cae al mapeo por defecto, y `--flip-mov` lo da la vuelta: una linea para
    desempatar sin tener que adivinar si el giro es de 180 grados (los dos ejes)
    o un espejo (uno solo).
    """
    H = cfg.get("H")
    # float32 como en fit_homography: perspectiveTransform exige que el pixel y
    # la matriz sean del mismo tipo.
    mapa = _wasd_de_H(np.array(H, np.float32), cal[0], cal[1]) if H \
        else dict(LETRAS)
    if flip & 1:
        mapa["d"], mapa["a"] = mapa["a"], mapa["d"]
    if flip & 2:
        mapa["w"], mapa["s"] = mapa["s"], mapa["w"]
    return mapa


def _flecha(k, ext):
    """Flecha de OpenCV como '-X'/'+X'/'-Y'/'+Y', o None si no es flecha.

    `ext` dice si el codigo vino precedido de ARROW_EXT, y es la unica pista
    para mirar SCAN: sin ella, 0x48 y 0x4B son tambien H y K en mayusculas y
    pulsarlas moveria el cabezal en vez de escribir. VK y NUMPAD si se pueden
    mirar sueltos porque sus codigos (%&'(" y !"#$) no chocan con ninguna letra.
    """
    if ext:
        return SCAN.get(k)
    if k > 0xFF:
        return VK.get((k >> 16) & 0xFF)
    return VK.get(k) or NUMPAD.get(k)


# Prefijo de tecla extendida ('\x00' o '\xe0') que ya se leyo de la consola
# y esta esperando su scan code. Vive fuera de _tecla porque se lee y se
# consume en llamadas distintas.
_PEND = []


def _tecla(mapa=None):
    """(codigo, direccion) de la siguiente tecla, o (-1, None) si no hay.

    ESTA ES LA PIEZA QUE FALTABA. cv2.waitKey solo ve las teclas si la ventana
    de OpenCV tiene el foco del teclado, y msvcrt solo las ve si el foco lo
    tiene la consola. Leyendo solo una de las dos, hay que acertar cual de las
    dos tiene el foco; en cuanto se clica en la otra, el programa se queda
    mudo sin decir nada, que es lo que pasaba con las flechas y con la q. Se
    leen las dos y gana la primera que conteste, asi que da igual cual tenga el
    foco. El msvcrc devuelve '\x00' o '\xe0' antes del scan code de las
    teclas especiales, y los scan codes suyos son otros que los de OpenCV.

    `mapa` es el de WASD deducido de la homografia; si no se pasa, el de por
    defecto. Las flechas NO se voltean: sus tablas ya son la direccion que
    espera el ojo, y dependen solo del teclado, no de como este montada la
    camara.
    """
    letras = LETRAS if mapa is None else mapa
    if msvcrt is not None and msvcrt.kbhit():
        c = msvcrt.getwch()
        if c in ("\x00", "\xe0"):
            # Una tecla extendida llega en DOS golpes: el prefijo '\xe0' y mas
            # tarde el scan code, y el hueco entre los dos es unpredictable.
            # getwch() no se puede usar para esperar, asi que en vez de
            # esperar se APUNTA que hay un prefijo pendiente y el scan code se
            # lee en la vuelta siguiente, cuando ya haya llegado. Antes se
            # perdia la flecha entera en silencio y solo salia un 0x0.
            _PEND.append(c)
            return (-1, None)
        # Si hay un prefijo pendiente, este caracter es su scan code... pero
        # solo si de verdad esta en la tabla. En esta maquina el segundo byte
        # no llega nunca, y sin esta comprobacion el prefijo se comeria la
        # SIGUIENTE tecla (pulsas flecha, luego 'w', y la w desaparece). Si no
        # es un scan code conocido se trata como tecla normal y no se pierde.
        n = ord(c) if c else -1
        if _PEND:
            _PEND.clear()
            d = MS_SCAN.get(n) or MS_NUMPAD.get(n)
            if d is not None:
                return (0, d)
        return (n, letras.get(chr(n).lower()) if n > 0 else None)
    k = cv2.waitKey(30)
    if k == ARROW_EXT:                   # forma extendida de OpenCV
        k2 = cv2.waitKey(10)
        return (k2 if k2 >= 0 else 0, SCAN.get(k2))
    return (k, _flecha(k, False) or
            (letras.get(chr(k).lower()) if 0 < k < 0x80 else None))
# Pulsos deadbeat de 1, 20 y 100 ms -> 0.2, 0.44 y 3.4 mm con el perfil lento.
STEP_MS = (1, 20, 100)
STEP_MM = (0.2, 0.44, 3.4)
PASO_MIN, PASO_MAX = 0.1, 10.0       # lo que se puede pedir con -/+


def ms_de_paso(mm):
    """Cuantos ms de pulsado deadbeat dan `mm` de desplazamiento.

    Al reves de STEP_MS/STEP_MM: la app deja elegir el paso en mm, como
    LightBurn, y el control solo entiende pulsos. Se interpola entre los tres
    puntos medidos y se extrapola con la pendiente del tramo final, con un tope
    de 1 ms: por debajo la Ruida no distingue pulsos de keyup, asi que un paso
    mas fino es indistinguible de "no mover"."""
    mm = max(PASO_MIN, min(PASO_MAX, float(mm)))
    ms = STEP_MS[0]
    for i in range(len(STEP_MM) - 1):
        a, b = STEP_MM[i], STEP_MM[i + 1]
        if mm <= b:
            f = (mm - a) / (b - a)
            return max(1, int(round(STEP_MS[i] + f * (STEP_MS[i + 1] - STEP_MS[i]))))
    # por encima del ultimo punto medido, misma pendiente que 0.44->3.4
    pend = (STEP_MS[-1] - STEP_MS[-2]) / (STEP_MM[-1] - STEP_MM[-2])
    return max(1, int(round(STEP_MS[-1] + pend * (mm - STEP_MM[-1]))))


def _escala(im, alto):
    h, w = im.shape[:2]
    return cv2.resize(im, (max(1, int(w * alto / h)), alto),
                      interpolation=cv2.INTER_AREA)


def _px_de_mm(Hinv, mm, cal, shape):
    """mm de maquina -> pixeles de la cenital, ya escalados al frame que se
    esta dibujando. Hinv devuelve pixeles de la resolucion con la que se calibro
    (cal), que no tiene por que ser la del frame actual."""
    q = cv2.perspectiveTransform(
        np.array([[[mm[0], mm[1]]]], np.float64), Hinv)[0, 0]
    return q[0] * shape[1] / cal[0], q[1] * shape[0] / cal[1]


def _barra(im, txt, color=(255, 255, 255)):
    """Franja con el nombre de cada mitad, para saber cual es cual. Sin esto
    eran dos camaras pegadas sin etiqueta y no se sabia cual era cual."""
    h = 34
    cv2.rectangle(im, (0, 0), (im.shape[1], h), (0, 0, 0), -1)
    cv2.putText(im, txt, (im.shape[1] // 2 - 8 * len(txt), 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)


def _texto(im, txt, org, color, escala=0.7):
    cv2.putText(im, txt, org, cv2.FONT_HERSHEY_SIMPLEX, escala, (0, 0, 0), 3,
                cv2.LINE_AA)               # sombra, para que se lea de todo
    cv2.putText(im, txt, org, cv2.FONT_HERSHEY_SIMPLEX, escala, color, 1,
                cv2.LINE_AA)


def mirar(m, top, head, i, n, flip=0, verbose=True):
    """Las dos camaras a la vez y el cabezal a W A S D, hasta Enter.

    Izquierda: la del cabezal, que es donde la marca tiene que quedar centrada
    y por eso va con la cruz y la ventana de busqueda dibujadas. Derecha: la
    cenital, para ver el contexto y elegir el siguiente punto, con un circulo
    rojo en donde esta el cabezal ahora y una flecha por letra que dice hacia
    donde lleva cada una (si la W sale hacia abajo, el eje va al reves).

    Las flechas van en pasos deadbeat y 'v' cambia de velocidad, asi que se
    llega al punto a base de pasos de 0.2 mm sin depender de LightBurn. Enter
    acepta, q sale.
    """
    win = "calibra punto %d/%d" % (i, n)
    # Ventana por defecto (autosize) y no WINDOW_NORMAL: con un imshow de tamano
    # fijo cada vuelta, el backend de Windows reajusta la ventana al de la
    # imagen y pelea con lo que haga el usuario. Es el mismo patron que usa
    # click_point, que por eso no da guerra.
    cv2.namedWindow(win)
    Hinv = None
    cal = tuple(m.cfg.get("top_res") or m.cfg["res"])
    if m.cfg.get("H"):
        try:
            Hinv = np.linalg.inv(np.array(m.cfg["H"], float))
        except np.linalg.LinAlgError:
            Hinv = None
    mapa = _mapa_wasd(m.cfg, cal, flip)
    pos, vel, ultima = None, 1, 0.0
    del _PEND[:]
    if verbose:
        print("\npunto %d/%d: mueve con W A S D (o con las flechas), 'v' "
              "cambia de velocidad, Enter acepta, q sale" % (i, n), flush=True)
        print("  W %s  A %s  S %s  D %s   (%s)" %
              (mapa["w"], mapa["a"], mapa["s"], mapa["d"],
               "deducido de la homografia" if Hinv is not None
               else "sin homografia aun: mapeo por defecto"), flush=True)
    while True:
        # Un fotograma por camara y sin Laplacian: aqui se navega, no se apunta.
        img = _escala(cv2.cvtColor(read_gris(head), cv2.COLOR_GRAY2BGR), 720)
        # Preguntar la posicion cuesta hasta un timeout entero si la Ruida no
        # contesta, y eso se come el bucle: a 1 Hz no se puede ir haciendo
        # jog. Se pregunta dos veces por segundo y el resto se dibuja con la
        # ultima que se sepa.
        ahora = time.time()
        if ahora - ultima > 0.5:
            ultima = ahora
            p = m.pos(0.6)
            if p:
                pos = p
        _barra(img, "CABEZAL")
        _texto(img, "PUNTO %d/%d" % (i, n), (12, 66), (255, 255, 255), 0.7)
        if pos:
            _texto(img, "%.1f, %.1f mm" % pos, (12, 100), (0, 255, 255))
        elif not m.ok:
            _texto(img, "SIN RESPUESTA DEL PANEL (50207)", (12, 100), (0, 0, 255))
        else:
            _texto(img, "posicion: leyendo...", (12, 100), (0, 165, 255))
        _texto(img, "paso %.1f mm" % STEP_MM[vel], (12, 130), (0, 255, 255))
        _texto(img, "W A S D  mover   v  velocidad   Enter  ok   q  salir",
               (12, img.shape[0] - 14), (255, 255, 255), 0.6)
        h0, w0 = img.shape[:2]
        cx, cy = w0 // 2, h0 // 2
        # ventana util de la busqueda, para ver si la marca cabe dentro
        pad = int(min(w0, h0) * 0.35)
        cv2.rectangle(img, (cx - pad, cy - pad), (cx + pad, cy + pad),
                      (0, 200, 0), 1)
        cv2.line(img, (cx - 25, cy), (cx + 25, cy), (0, 255, 255), 1)
        cv2.line(img, (cx, cy - 25), (cx, cy + 25), (0, 255, 255), 1)

        gt = _escala(cv2.cvtColor(read_gris(top), cv2.COLOR_GRAY2BGR), 720)
        if Hinv is not None and pos:
            # perspectiveTransform ya divide por la tercera coordenada y
            # devuelve 2D: no hay que normalizar a mano como con H @ [x,y,1].
            tx, ty = _px_de_mm(Hinv, pos, cal, gt.shape)
            tx, ty = int(tx), int(ty)
            if 0 <= tx < gt.shape[1] and 0 <= ty < gt.shape[0]:
                cv2.circle(gt, (tx, ty), 14, (0, 0, 255), 2)
                cv2.line(gt, (tx - 20, ty), (tx + 20, ty), (0, 0, 255), 1)
                # Hacia donde lleva cada letra en la cenital. Se ve de un
                # vistazo si W va hacia abajo, que era el fallo que reportaba.
                for letra in "wasd":
                    dj = mapa[letra]
                    dx, dy = (8.0, 0.0) if dj.endswith("X") else (0.0, 8.0)
                    if dj.startswith("-"):
                        dx, dy = -dx, -dy
                    ax, ay = _px_de_mm(Hinv, (pos[0] + dx, pos[1] + dy),
                                       cal, gt.shape)
                    cv2.arrowedLine(gt, (tx, ty), (int(ax), int(ay)),
                                    (0, 255, 255), 1, tipLength=0.35)
                    cv2.putText(gt, letra, (int(ax) - 5, int(ay) + 14),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255),
                                1, cv2.LINE_AA)
        _barra(gt, "CENITAL")
        gap = np.full((720, 6, 3), 40, np.uint8)
        cv2.imshow(win, np.hstack([img, gap, gt]))

        k, d = _tecla(mapa)
        if k in (ord("q"), 27):
            cv2.destroyWindow(win)
            return "q"
        if k == 13:
            cv2.destroyWindow(win)
            return "accept"
        if k == ord(VEL_KEY) or k == ord(VEL_KEY.upper()):
            vel = (vel + 1) % len(STEP_MS)
            print("  velocidad de paso: %.1f mm" % STEP_MM[vel],
                  flush=True)
        elif d:
            if m.pan:
                m.pan.hold(d, STEP_MS[vel])
            else:
                print("  sin panel: no se puede mover", flush=True)
        elif k == 0:
            # Prefijo de tecla extendida leido pero sin scan code detras: el
            # segundo byte no llego. Dice otra cosa que "codigo desconocido".
            print("  flecha sin scan code: llego el prefijo y no el segundo "
                  "byte", flush=True)
        elif k >= 0:
            # Eco de CUALQUIER tecla que no sea una orden, para que se vea que
            # lo que falla es el codigo y no el foco. Con las flechas de verdad
            # no tiene que salir nunca; si sale, el numero es el codigo crudo y
            # con el se anade la entrada que falte en las tablas de arriba.
            print("  tecla 0x%X (%r) sin asignar" % (k, chr(k)), flush=True)


def cmd_calibrate(a):
    cfg = load_cfg()
    if a.park:
        cfg["park"] = [float(v) for v in a.park.split(",")]
    if a.top is not None:
        cfg["top_cam"] = a.top
    cfg["res"] = [a.width, a.height]
    # Solo para desempatar a mano cuando la homografia aun no existe, que es la
    # primera vez que se calibra. Luego el sentido sale solo de la H.
    if a.flip_mov:
        cfg["wasd_flip"] = cfg.get("wasd_flip", 0) ^ 3
        print("sentido del movimiento dado la vuelta a mano (quedan los dos "
              "ejes al reves): revisa en la cenital que W apunte arriba")
    m = Machine(cfg)
    top = open_cam(cfg, "top")
    # La camara del cabezal no la usa la homografia, asi que queda libre para
    # usarla de puntero: ayuda a poner el cabezal encima de la referencia, que
    # es lo que decide la precision del punto (el par que se guarda es la
    # posicion del cabezal contra el pixel del clic).
    head = None if a.no_watch else open_cam(cfg, "head")
    px, mm = [], []
    try:
        for i in range(a.points):
            while True:
                if head is not None:
                    # Las dos camaras a la vez y W A S D mueven el cabezal:
                    # no hace falta LightBurn. Con --no-watch se cae al modo
                    # consola de antes (mover con el mando y escribir el X Y).
                    r = mirar(m, top, head, i + 1, a.points,
                              cfg.get("wasd_flip", 0))
                    if r == "q":
                        raise KeyboardInterrupt
                elif input().strip().lower() == "q":
                    raise KeyboardInterrupt
                if a.manual:
                    p = ask_position()
                else:
                    p = m.pos(2.0)
                    if p is None:
                        print("sin informe de posicion: reintenta o usa --manual")
                        continue
                break
            m.park()
            g = grab(top, a.frames)
            uv = click_point(g, "clic en la marca de referencia %d" % (i + 1))
            px.append(uv)
            mm.append(p)
            print("  pixel (%.1f, %.1f) = maquina (%.3f, %.3f)" % (uv + p))
        H, e_max, e_avg, malos = fit_homography(px, mm)
        if H is None:
            sys.exit("no queda ningun modelo: mide de nuevo, con puntos mas "
                     "separados y clica en el centro de la mancha")
        # Los puntos que RANSAC rechazo no se guardan: si se guardaran, la
        # proxima calibracion los volveria a meter y a contaminar el ajuste.
        cfg["H"] = H.tolist()
        cfg["points"] = [(px[i], mm[i]) for i in range(len(px)) if i not in malos]
        print("\nresiduo del ajuste: sobre %d puntos, max %.3f mm, medio %.3f mm"
              "  (0.1-0.2 mm es normal, no es un error)"
              % (len(cfg["points"]), e_max, e_avg))
        for i in malos:
            print("  DESCARTADO el punto %d (pixel %.1f, %.1f = maquina "
                  "%.3f, %.3f): no cuadra, normalmente por un clic en la "
                  "marca de al lado. Repetir ese punto si se repite."
                  % (i + 1, px[i][0], px[i][1], mm[i][0], mm[i][1]))
        if e_max > 1.0:
            print("AVISO: error alto entre los puntos aceptados. Mide con mas "
                  "precision o repite puntos.")
        save_cfg(cfg)
        print("fija el origen del laser en la esquina inferior izquierda "
              "y no lo cambies")
    finally:
        top.release()
        if head is not None:
            head.release()
        m.close()


def cmd_fov(a):
    """Mide el campo de vision de la camara del cabezal y lo guarda.

    Es lo unico que falta para el ajuste fino y no se puede deducir: hace
    falta una distancia real en la mesa (una regla, o dos marcas separadas
    un milimetro que sepas)."""
    cfg = load_cfg()
    cap = open_cam(cfg, "head")
    try:
        g = grab(cap, a.frames)
        h, w = g.shape
        p1, p2 = click_point(g, "clic en los dos extremos de una distancia "
                                 "que sepas de verdad", n=2)
        mm = float(input("esa distancia en la mesa, en mm: "))
        if mm <= 0 or p1 == p2:
            sys.exit("distancia no valida")
        k = mm / math.dist(p1, p2)          # mm por pixel
        cfg["head_fov_mm"] = k * w
        save_cfg(cfg)
        print("\nhead_fov_mm = %.2f   (%.4f mm/px sobre %d px de ancho)"
              % (cfg["head_fov_mm"], k, w))
        print("campo de vision vertical: %.2f mm" % (k * h))
    finally:
        cap.release()


def pick_pair(marks, found=None):
    """Elige dos marcas, priorizando el area de sus componentes detectados.

    Una mancha pequeña puede caer dentro del area segura por un reflejo y, si
    se elige solo por distancia, reemplazar una marca real. La distancia queda
    como desempate; sin `found` se conserva el criterio anterior.
    """
    pairs = itertools.combinations(marks, 2)
    if found is None:
        return max(pairs, key=lambda p: math.dist(p[0][0], p[1][0]))
    area_by_px = {(u, v): area for u, v, area in found}

    def score(pair):
        try:
            areas = [area_by_px[mark[1]] for mark in pair]
        except KeyError as e:
            raise ValueError("no se encontro el area de una marca detectada") from e
        return min(areas), sum(areas), math.dist(pair[0][0], pair[1][0])

    return max(pairs, key=score)


def area_trabajo():
    """El area de trabajo en mm, como (x0, y0, x1, y1): la caja de la Ruida.

    La homografia se ajusta con puntos repartidos por ahi, y fuera de la caja
    sus mm son extrapolacion: no son medidas, son suposiciones."""
    x0, x1, y0, y1 = ruida.Panel.SAFE
    return (x0, y0, x1, y1)


def marcas_utiles(found, H, roi_mm=None):
    """Reparte las manchas detectadas en (dentro, fuera) del area de trabajo.

    Cada marca sale como (mm, px). Lo de descartar ANTES de elegir el par es
    lo que quita los puntos fantasma: si se eligiera la pareja mas separada
    primero, el par seria el tag de calibracion y la mancha de un borde, no las
    dos marcas del material. Una mancha fuera de la caja es un tag o un
    reflejo, y ademas su mm viene de una homografia extrapolada (un tag en el
    borde daba 599 mm en una cama de 500x400)."""
    if roi_mm and len(roi_mm) == 4:
        x0, y0, x1, y1 = roi_mm
    else:
        x0, y0, x1, y1 = area_trabajo()
    dentro, fuera = [], []
    for u, v, area in found:
        mk = (px_to_mm(H, (u, v)), (u, v))
        (dentro if x0 <= mk[0][0] <= x1 and y0 <= mk[0][1] <= y1 else fuera).append(mk)
    return dentro, fuera


def cmd_run(a):
    cfg = load_cfg()
    if "H" not in cfg:
        sys.exit("falta calib.json: ejecuta antes 'calibrate'")
    H = np.array(cfg["H"], np.float32)
    marks_wanted = a.marks or cfg["marks"]
    # ROI opcional en mm de maquina: "x0,y0,x1,y1". En una cama llena de tags
    # de calibracion sirve para quedarse solo con las dos marcas del material.
    roi_mm = ([float(v) for v in a.roi.split(",")]
              if a.roi else cfg.get("roi_mm"))
    if roi_mm and len(roi_mm) != 4:
        sys.exit("--roi toma 4 numeros: x0,y0,x1,y1 en mm de maquina")
    # Sin --roi manda el area de trabajo: es la zona donde la homografia esta
    # medida, y fuera de ella sus mm son suposiciones, no medidas.
    roi_mm = tuple(roi_mm) if roi_mm else area_trabajo()
    m = Machine(cfg)
    top = open_cam(cfg, "top")
    head = open_cam(cfg, "head")
    out = []
    try:
        if a.no_move:
            p = cfg["park"]
            print("sin movimiento: solo deteccion desde el estacionamiento %s" % p)
        else:
            m.park()
        g = grab(top, a.frames)
        cv2.imwrite(BED_PNG, g)
        found = find_marks(g, min_area=cfg["min_area"], max_area=cfg["max_area"],
                           thr=cfg["thr"], show=a.debug)
        print("marcas detectadas: %d" % len(found))
        if len(found) < marks_wanted:
            sys.exit("faltan marcas (%d de %d). Mira %s y "
                     "ajusta thr o min_area en calib.json"
                     % (len(found), marks_wanted, BED_PNG))
        if len(found) > marks_wanted and not roi_mm:
            # p.ej. un aro con la cruz suelta dentro son DOS componentes, no
            # una marca. Sin esto el codigo elige las dos mas separadas y puede
            # emparejar un aro con la cruz de otra marca sin decir nada.
            # Con --roi el exceso es esperado: hay tags de calibracion por medio.
            print("AVISO: %d marcas detectadas pero esperabamos %d. Cada marca "
                  "debe ser UNA sola mancha oscura continua: un aro con la cruz "
                  "suelta dentro cuenta por dos. Mira %s con --debug y haz la "
                  "marca solida (disco relleno, o disco con la cruz en blanco "
                  "dentro)." % (len(found), marks_wanted, BED_PNG))
        # (mm, px) van juntos: al reordenar marcas hay que mover los dos
        dentro, fuera = marcas_utiles(found, H, roi_mm)
        if fuera:
            print("fuera del area de trabajo %s: %d manchas descartadas %s"
                  % (roi_mm, len(fuera),
                     [(round(mk[0][0], 1), round(mk[0][1], 1)) for mk in fuera]))
        if a.roi:
            print("ROI %s: %d de %d manchas dentro" % (roi_mm, len(dentro), len(found)))
        marks = dentro
        if len(marks) < marks_wanted:
            sys.exit("dentro del area de trabajo %s quedan %d manchas de las %d "
                     "pedidas.\nLo normal es que falten por tags de calibracion de "
                     "por medio): acota con --roi x0,y0,x1,y1 en mm.\n"
                     "Si las que sobran estan FUERA de la caja, la homografia "
                     "extrapola mal ahi: repite 'calibrate' con puntos "
                     "repartidos por TODA la cama, esquinas incluidas."
                     % (roi_mm, len(marks), marks_wanted))
        if marks_wanted == 2:
            a_, b_ = pick_pair(marks, found)
            extras = [mark for mark in marks if mark not in (a_, b_)]
            if extras:
                area_by_px = {(u, v): area for u, v, area in found}
                print("candidatas adicionales dentro del area segura, "
                      "descartadas al elegir por tamano: %s"
                      % [(tuple(round(q, 1) for q in mark[1]),
                          area_by_px[mark[1]]) for mark in extras])
            marks = [a_, b_]
        else:
            marks = marks[:marks_wanted]
        for i, ((mx, my), (u, v)) in enumerate(marks, 1):
            print("marca %d: %.3f, %.3f mm (px %.1f, %.1f)" % (i, mx, my, u, v))
            if a.no_move:
                out.append((mx, my))
                continue
            m.goto(mx, my)
            pos = fine(m, head, cfg, a)
            out.append(pos)
        ox, oy = cfg["cam_offset_mm"]
        print("\ncoordenadas para el modulo Print and Cut de LightBurn:")
        for i, (x, y) in enumerate(out, 1):
            print("  marca %d:  X = %.3f   Y = %.3f" % (i, x + ox, y + oy))
        save_txt(out, cfg, a)
        if a.no_move:
            return marks
    finally:
        for c in (top, head):
            c.release()
        try:
            pass  # el teclado no tiene comando de stop: se suelta con keyup
        except Exception as e:
            print("AVISO: stop no enviado: %s" % e)
        m.close()


def center_roi(w, h, frac=0.35):
    """Caja centrada, dentro del frame. Con el driver dando una resolucion
    distinta a la pedida, un pad calculado sobre w se sale de h y numpy lee
    el indice negativo desde el final."""
    pad = int(min(w, h) * frac)
    return (max(0, w // 2 - pad), max(0, h // 2 - pad),
            min(w, w // 2 + pad), min(h, h // 2 + pad))


def fine(m, head, cfg, a):
    """Recentra la marca con la camara del cabezal. Devuelve la posicion real."""
    fov = cfg["head_fov_mm"]
    for i in range(a.iters):
        g = grab(head, a.frames)
        # medida sobre el frame real, no sobre la res que pedimos
        h, w = g.shape
        roi = center_roi(w, h)
        found = find_marks(g, roi=roi, min_area=cfg["min_area"],
                           max_area=cfg["max_area"], thr=cfg["thr"])
        if not found:
            print("  iter %d: no veo la marca, reviso iluminacion/FOV" % (i + 1))
            return m.pos() or (0.0, 0.0)
        u, v, area = found[0]
        # la camara puede estar girada o espejada: por eso los signos son config
        dx = (u - w / 2.0) * fov / w * cfg["head_flip_x"]
        dy = (v - h / 2.0) * fov / w * cfg["head_flip_y"]
        print("  iter %d: offset %.3f, %.3f mm (px %.1f, %.1f, area %d)"
              % (i + 1, dx, dy, u, v, area))
        if math.hypot(dx, dy) < a.tol:
            print("  centrado")
            return m.pos() or (0.0, 0.0)
        step = math.hypot(dx, dy)
        if step > a.max_step:
            dx, dy = dx * a.max_step / step, dy * a.max_step / step
            print("  paso recortado a %.2f mm" % a.max_step)
        # destino absoluto + lazo cerrado: la velocidad de jog es una
        # rampa, no una constante, asi que un salto abierto de "dx mm"
        # se queda corto. move_to mide, corrige y vuelve a medir.
        p = m.pos() or (0.0, 0.0)
        m.pan.move_to(p[0] - dx, p[1] - dy, tol=a.tol)
    print("AVISO: no se centro en %d iteraciones" % a.iters)
    return m.pos() or (0.0, 0.0)


def save_txt(out, cfg, a):
    if not a.emit:
        return
    ox, oy = cfg["cam_offset_mm"]
    with open(a.emit, "w") as f:
        for i, (x, y) in enumerate(out, 1):
            f.write("marca %d  X=%.3f  Y=%.3f\n" % (i, x + ox, y + oy))
    print("coordenadas en %s" % a.emit)


def cmd_scan(a):
    """En Windows el indice depende del orden de conexion: hay que ver cual es cual.
    Abre cada indice, guarda una foto y dice la resolucion real."""
    seen = []
    for idx in range(a.max_idx):
        cap = None
        for be in BACKENDS:
            cap = cv2.VideoCapture(idx, be)
            if cap.isOpened():
                break
            cap.release()
        if not cap.isOpened():
            continue
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        ok, f = None, None
        for _ in range(8):
            ok, f = cap.read()
            if ok:
                break
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if ok:
            path = os.path.join(HERE, "scan_idx%d.png" % idx)
            cv2.imwrite(path, f)
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            # huella: dice si la camara ve algo o solo el tapon
            seen.append((idx, w, h, path))
            print("indice %d: %dx%d  brillo %.0f  contraste %.0f  nitidez %.0f"
                  "  ->  %s" % (idx, w, h, g.mean(), g.std(),
                                cv2.Laplacian(g, cv2.CV_64F).var(),
                                os.path.basename(path)))
        else:
            print("indice %d: abre pero no entrega imagen (%dx%d)" % (idx, w, h))
        cap.release()
    print("\n%d camaras. Mira los scan_idx*.png y pon el indice de la cenital "
          "en top_cam y el del cabezal en head_cam (calib.json)." % len(seen))
    print("Si las IMX179 dan dos resoluciones distintas, no las confundas: "
          "la del cabezal es la que va montada en el cabezal.")


def cmd_cams(a):
    cfg = load_cfg()
    cfg["res"] = [a.width, a.height]
    if a.top is not None:
        cfg["top_cam"] = a.top
    if a.head is not None:
        cfg["head_cam"] = a.head
    top, head = open_cam(cfg, "top"), open_cam(cfg, "head")
    # ambas a la misma altura, y el conjunto reducido para que quepa en pantalla
    win = "vision hibrida"
    # misma logica que en mirar(): ventana por defecto, sin WINDOW_NORMAL
    cv2.namedWindow(win)
    print("q para salir")
    try:
        while True:
            ok1, f1 = top.read()
            ok2, f2 = head.read()
            if not ok1 or not ok2:
                break
            hh = min(f1.shape[0], f2.shape[0])
            pan = []
            for f, name, key in ((f1, "cenital", "top"), (f2, "cabezal", "head")):
                p = cv2.resize(f, (int(f.shape[1] * hh / f.shape[0]), hh))
                # etiqueta propia: si un panel sale negro o no se ve, se sabe cual
                cv2.putText(p, "%s  idx%d  %dx%d"
                            % (name, cfg[key + "_cam"], p.shape[1], hh),
                            (10, 28), 0, 0.8, (0, 255, 255), 2)
                pan.append(p)
            v = np.hstack(pan)
            if v.shape[1] > 1600:
                v = cv2.resize(v, (1600, int(v.shape[0] * 1600 / v.shape[1])))
            cv2.imshow(win, v)
            if (cv2.waitKey(30) & 0xFF) in (ord("q"), 27):
                break
    finally:
        top.release()
        head.release()
        cv2.destroyAllWindows()


def test_mirar():
    """Recorre mirar() entero sin camara, que es la unica forma de probar el
    mando de las flechas. Todo lo de OpenCV (imshow, namedWindow, waitKey) se
    sustituye por una lista de teclas guionizada; lo demas -- grab, escalado,
    el circulo de la H invertida y el pulso que sale en la Ruida -- se ejecuta
    de verdad. Sin esto, la unica forma de saber si una flecha va al eje
    correcto es mover el cabezal de verdad, y ya ha fallado dos veces por
    codigo sin que salte ningun error."""

    pulsos = []

    class Pan:
        def hold(self, d, ms):
            pulsos.append((d, ms))

    class Maq:
        # H que refleja una cenital BIEN MONTADA: la Y de la maquina (arriba)
        # cae hacia arriba en la imagen, asi que el signo de la fila Y sale
        # negativo. Con esta H el deduce sale el mapeo de siempre, y 200,100 mm
        # cae dentro del frame cenital (pixel 400,490), que es lo que hace
        # aparecer el circulo rojo.
        cfg = {"H": [[0.5, 0.0, 0.0], [0.0, -0.5, 540.0], [0.0, 0.0, 1.0]],
               "res": [1920, 1080]}
        pan = Pan()
        ok = True

        def pos(self, t=1.0):
            return (200.0, 100.0)

    class Cap:
        def __init__(self, h, w):
            self.im = np.full((h, w, 3), 128, np.uint8)

        def read(self):
            return True, self.im

    # +X, 's' (pasa a 3.4 mm), +X, -Y, luego la forma extendida de una
    # flecha (ARROW_EXT y a continuacion el scan code) y Enter.
    teclas = [0x270000, ord(VEL_KEY), 0x270000, 0x280000, ARROW_EXT, 0x4B, 13]
    n_teclas = len(teclas)
    vistas = []
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    msvcrt_orig = msvcrt
    globals()["msvcrt"] = None      # el guion lo lleva cv2.waitKey
    cv2.namedWindow = lambda *a, **k: None
    cv2.imshow = lambda w, im: vistas.append(im)
    cv2.destroyWindow = lambda *a: None
    cv2.waitKey = lambda d: teclas.pop(0) if teclas else -1
    try:
        r = mirar(Maq(), Cap(720, 1280), Cap(1080, 1920), 1, 6, verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig

    assert r == "accept", r
    # La velocidad arranca en el indice 1 (0.44 mm) y 's' la lleva al 2.
    assert pulsos == [("+X", 20), ("+X", 100), ("-Y", 100), ("-X", 100)], pulsos

    # Las dos camaras a la misma altura, lado a lado con el separador.
    assert len(vistas) == n_teclas - 1, (len(vistas), n_teclas)
    assert vistas[0].shape == (720, 1280 + 6 + 1280, 3), vistas[0].shape
    # Con la cenital bien montada, 200,100 mm cae dentro del frame cenital: tiene
    # que haberse dibujado el circulo rojo.
    assert ((vistas[0][:, :, 2] > 200) & (vistas[0][:, :, 0] < 60)).any(), \
        "no se dibujo el circulo de la posicion del cabezal"

    # 'q' sale sin mover nada.
    teclas[:] = [ord("q")]
    pulsos[:] = []
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    msvcrt_orig = msvcrt
    globals()["msvcrt"] = None      # el guion lo lleva cv2.waitKey
    cv2.namedWindow = lambda *a, **k: None
    cv2.imshow = lambda w, im: None
    cv2.destroyWindow = lambda *a: None
    cv2.waitKey = lambda d: teclas.pop(0) if teclas else -1
    try:
        r = mirar(Maq(), Cap(720, 1280), Cap(1080, 1920), 1, 6, verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig
    assert r == "q" and pulsos == [], (r, pulsos)

    # Y lo mismo pero pulsando desde la CONSOLA, que es la mitad del arreglo:
    # msvcrt devuelve '\xe0' y luego el scan code. '\xe0'+'P' es la izquierda,
    # y en msvcrt la izquierda es P, no K como en OpenCV.
    class Consola:
        def __init__(self, scrip):
            self.buf = list(scrip)

        def kbhit(self):
            return bool(self.buf)

        def getwch(self):
            return self.buf.pop(0)

    teclas[:] = []
    pulsos[:] = []
    msvcrt_orig = msvcrt
    globals()["msvcrt"] = Consola(["\xe0", "P", "q"])
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    cv2.namedWindow = lambda *a, **k: None
    cv2.imshow = lambda w, im: None
    cv2.destroyWindow = lambda *a: None
    cv2.waitKey = lambda d: -1        # la ventana no ve nada: manda la consola
    try:
        r = mirar(Maq(), Cap(720, 1280), Cap(1080, 1920), 1, 6, verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig
    assert pulsos == [("-X", 20)], pulsos
    assert r == "q", r

    # Un prefijo huerfano (flecha cuyo segundo byte no llega) NO se puede
    # comer la tecla siguiente. Antes la w desaparecia detras de la flecha.
    globals()["msvcrt"] = Consola(["\xe0", "w", "q"])
    pulsos[:] = []
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    cv2.namedWindow = lambda *a, **k: None
    cv2.imshow = lambda w, im: None
    cv2.destroyWindow = lambda *a: None
    cv2.waitKey = lambda d: -1
    try:
        r = mirar(Maq(), Cap(720, 1280), Cap(1080, 1920), 1, 6, verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig
    assert pulsos == [("+Y", 20)], ("la w se perdio tras el prefijo", pulsos)

    # Y las letras, que es como se mueve de verdad: W A S D. Llegan en un solo
    # golpe y en mayusculas tambien, por si el Bloq Mayus esta puesto.
    class Simple:
        def __init__(s2, scrip):
            s2.buf = list(scrip)

        def kbhit(s2):
            return bool(s2.buf)

        def getwch(s2):
            return s2.buf.pop(0)

    pulsos[:] = []
    globals()["msvcrt"] = Simple(list("waSD" + VEL_KEY + "dq"))
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    cv2.namedWindow = lambda *a, **k: None
    cv2.imshow = lambda w, im: None
    cv2.destroyWindow = lambda *a: None
    cv2.waitKey = lambda d: -1
    try:
        r = mirar(Maq(), Cap(720, 1280), Cap(1080, 1920), 1, 6, verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig
    # w +Y, a -X, S -Y (mayuscula tambien), D +X, luego la v sube el paso a
    # 100 ms y la d siguiente ya sale a 3.4 mm. La v sola no mueve, por eso
    # hace falta una letra detras para ver que el cambio surtio.
    assert pulsos == [("+Y", 20), ("-X", 20), ("-Y", 20), ("+X", 20),
                      ("+X", 100)], pulsos
    assert r == "q", r

    # El caso que de verdad fallaba: el prefijo '\xe0' y el scan code NO
    # llegan en el mismo instante. getwch() con el buffer vacio devuelve "" y
    # la flecha se perdia en silencio. Este guion saca el prefijo y aun tiene
    # que pasar por un kbhit() en falso antes de dar el scan code.
    class ConsolaTarda:
        """El scan code no esta ahi todavia: la consola dice que no hay nada
        un par de veces seguidas, que es como llega de verdad."""

        def __init__(self):
            self.buf = ["\xe0", "P", "q"]
            self.polls = 0

        def kbhit(self):
            if not self.buf:
                return False
            if self.buf[0] == "\xe0":
                self.polls += 1
                if self.polls < 3:        # el segundo byte aun no ha llegado
                    return False
            return True

        def getwch(self):
            return self.buf.pop(0)

    teclas[:] = []
    pulsos[:] = []
    msvcrt_orig = msvcrt
    globals()["msvcrt"] = ConsolaTarda()
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    cv2.namedWindow = cv2.imshow = cv2.destroyWindow = lambda *a, **k: None
    cv2.waitKey = lambda d: -1
    try:
        r = mirar(Maq(), Cap(720, 1280), Cap(1080, 1920), 1, 6, verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig
    assert pulsos == [("-X", 20)], ("scan code tardio", pulsos)
    assert r == "q", r

    # El caso que reporto el usuario: con la cenital girada 180 grados, que es
    # como esta montada ahora mismo, el deduce tiene que dar los cuatro ejes al
    # reves SIN que nadie escriba el mapeo a mano. Antes estaba escrito en el
    # codigo y por eso ya no cuadraba con la camara.
    class MaqGirada:
        cfg = {"H": [[-0.5, 0.0, 1920.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]],
               "res": [1920, 1080]}
        pan = Pan()
        ok = True

        def pos(self, t=1.0):
            return (200.0, 100.0)

    pulsos[:] = []
    globals()["msvcrt"] = Simple(list("waSDq"))
    orig = cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey
    cv2.namedWindow = cv2.imshow = cv2.destroyWindow = lambda *a, **k: None
    cv2.waitKey = lambda d: -1
    try:
        r = mirar(MaqGirada(), Cap(720, 1280), Cap(1080, 1920), 1, 6,
                  verbose=False)
    finally:
        cv2.imshow, cv2.namedWindow, cv2.destroyWindow, cv2.waitKey = orig
        globals()["msvcrt"] = msvcrt_orig
    assert pulsos == [("-Y", 20), ("+X", 20), ("+Y", 20), ("-X", 20)], pulsos
    assert r == "q", r

    # Y el deduce en si, sin pasar por mirar(): bien montada, girada 180 grados
    # y espejada solo en horizontal (que es lo que distingue un giro de un
    # espejo, y por eso --flip-mov va por ejes y no a ojo).
    Hn = [[0.5, 0.0, 0.0], [0.0, -0.5, 540.0], [0.0, 0.0, 1.0]]
    Hg = [[-0.5, 0.0, 1920.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]]
    Hm = [[-0.5, 0.0, 1920.0], [0.0, -0.5, 540.0], [0.0, 0.0, 1.0]]
    deduce = lambda h: _wasd_de_H(np.array(h, np.float32), 1920, 1080)
    assert deduce(Hn) == {"d": "+X", "a": "-X", "w": "+Y", "s": "-Y"}, deduce(Hn)
    assert deduce(Hg) == {"d": "-X", "a": "+X", "w": "-Y", "s": "+Y"}, deduce(Hg)
    assert deduce(Hm) == {"d": "-X", "a": "+X", "w": "+Y", "s": "-Y"}, deduce(Hm)
    assert _op("+X") == "-X" and _op("-Y") == "+Y"
    # Sin homografia sale el mapeo de por defecto, y --flip-mov lo voltea por
    # ejes encima (1 = derecha/izquierda, 2 = arriba/abajo).
    sin = {"H": None}
    assert _mapa_wasd(sin, (1920, 1080)) == LETRAS
    assert _mapa_wasd(sin, (1920, 1080), 1) == deduce(Hm)
    assert _mapa_wasd(sin, (1920, 1080), 2) == \
        {"d": "+X", "a": "-X", "w": "-Y", "s": "+Y"}
    assert _mapa_wasd(sin, (1920, 1080), 3) == deduce(Hg)
    assert _mapa_wasd({"H": Hn}, (1920, 1080), 1) == deduce(Hm)

    print("teclado: OK (WASD y flechas, ventana de OpenCV y consola, "
          "con el scan code tardio)")
    print("sentido de WASD: OK (deducido de la H: bien montada, girada 180 y "
          "espejada; y --flip-mov por ejes)")
    print("mirar: OK (4 ejes por letra y por flecha, v cambia de paso, circulo rojo, q sale)")



def cmd_test(a):
    import ruida as r
    r.selftest()
    # homografia sintetica: px -> mm lineal + traslacion
    T = np.array([[0.12, 0.03, 40.0], [-0.02, 0.11, 25.0], [0, 0, 1]], np.float32)
    src = [(100, 200), (1800, 200), (100, 1000), (1800, 1000), (900, 600)]
    mm = [cv2.perspectiveTransform(np.array([[[u, v]]], np.float32), T)[0, 0]
          for u, v in src]
    H, e_max, _, _ = fit_homography(src, mm)
    for (u, v), want in zip(src, mm):
        got = px_to_mm(H, (u, v))
        assert math.dist(got, (want[0], want[1])) < 1e-3, (got, want)
    print("homografia: OK (%d puntos, error max %.2e mm)" % (len(src), e_max))
    g = np.full((1080, 1920), 255, np.uint8)
    for u, v in ((300, 200), (960, 540)):
        cv2.circle(g, (u, v), 6, 0, -1)
    found = find_marks(g, min_area=8)
    assert len(found) == 2, found
    for u, v, _ in found:
        want = min(((300, 200), (960, 540)), key=lambda w: math.dist((u, v), w))
        assert math.dist((u, v), want) < 0.01, (u, v, want)
    print("deteccion: OK (2 marcas, centro subpixel)")
    # El tag de LightBurn es un aro con la cruz dentro: dos blobs con el mismo
    # centroide. merge tiene que devolver UNA marca en el centro, no dos.
    for r in (10, 20, 30):
        g = np.full((400, 600), 255, np.uint8)
        cv2.circle(g, (300, 200), r, 0, 2, cv2.LINE_AA)
        a = r // 2
        cv2.line(g, (300 - a, 200), (300 + a, 200), 0, 2, cv2.LINE_AA)
        cv2.line(g, (300, 200 - a), (300, 200 + a), 0, 2, cv2.LINE_AA)
        assert len(find_marks(g, merge=0)) == 2, ("sin merge deben ser 2", r)
        f = find_marks(g, merge=6)
        assert len(f) == 1, ("aro r=%d px -> %d marcas" % (r, len(f)))
        assert math.dist(f[0][:2], (300, 200)) < 0.5, (r, f)
    # tags separados no se fusionan
    g = np.full((400, 600), 255, np.uint8)
    for cx in (120, 480):
        cv2.circle(g, (cx, 200), 20, 0, 2, cv2.LINE_AA)
    assert len(find_marks(g, merge=6)) == 2
    print("tag aro+cruz: OK (1 marca a cualquier radio, 2 tags separados = 2)")
    # En la vista de calibracion, los candidatos circulares evitan iluminar
    # bordes y reflejos alargados que el umbral global ve como componentes.
    g = np.full((300, 500), 255, np.uint8)
    for cx in (120, 260):
        cv2.circle(g, (cx, 150), 12, 0, -1)
    cv2.ellipse(g, (420, 40), (35, 12), 0, 0, 360, 0, -1)
    assert len(find_marks(g, min_area=40, min_circularity=0.55,
                          max_aspect_ratio=1.5)) == 2
    print("filtro calibracion: OK (conserva discos y descarta reflejo alargado)")
    # el driver dio 1280x720 y no lo que pedimos: la ROI tiene que caber igual
    for w, h in ((1920, 1080), (1280, 720), (640, 480), (320, 240)):
        x0, y0, x1, y1 = center_roi(w, h)
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, (w, h, center_roi(w, h))
    # 720p con pad sobre w daba y0 negativo: numpy lo leeria desde el final
    x0, y0, x1, y1 = center_roi(1280, 720)
    assert y0 > 0 and (y1 - y0) > 100, center_roi(1280, 720)
    print("ROI: OK (caja centrada y dentro del frame en 4 resoluciones)")

    # El filtro de la caja de la maquina descarta los tags que la homografia
    # extrapola fuera de la cama. Ojo: SAFE va como (x0, x1, y0, y1) y desempaquetarlo
    # como (x0, y0, x1, y1) hace que TODO salga fuera. Este test lo caza.
    # ("r" esta reasignado por un for de mas arriba, se usa el modulo)
    sx0, sx1, sy0, sy1 = ruida.Panel.SAFE
    assert (sx0, sx1, sy0, sy1) == (0.0, 500.0, 0.0, 400.0), ruida.Panel.SAFE
    dentro = lambda mm: (sx0 <= mm[0] <= sx1 and sy0 <= mm[1] <= sy1)
    assert dentro((250.0, 200.0)), "un punto en mitad de cama tiene que entrar"
    assert not dentro((599.073, 412.221)), "fuera de la caja tiene que salir"
    assert not dentro((477.532, -4.142)), "y por Y negativa tambien"
    print("filtro de caja: OK (dentro 250,200 entra; 599,412 y 477,-4.1 salen)")

    # El filtro de verdad, con la H sintetica de arriba: mm = 0.12u+0.03v+40,
    # -0.02u+0.11v+25. (0,0) cae en 40,25 mm (dentro de la cama de 500x400) y
    # (4000,0) en 520,-55 (fuera). El ROI explicito manda sobre la caja.
    dentro, fuera = marcas_utiles([(0, 0, 900), (4000, 0, 800)], T)
    assert len(dentro) == 1 and len(fuera) == 1, (dentro, fuera)
    assert dentro[0][0] == (40.0, 25.0) and dentro[0][1] == (0, 0), dentro
    d2, f2 = marcas_utiles([(0, 0, 900), (4000, 0, 800)], T, (0, 0, 10, 10))
    assert not d2 and len(f2) == 2, (d2, f2)
    print("marcas_utiles: OK (la caja descarta el tag de fuera; el ROI manda)")
    # Una mota puede proyectarse dentro del area segura y ser mas lejana que
    # las dos marcas reales. La pareja se decide por el tamano de componente,
    # usando la distancia solo para desempatar.
    candidatas = [(px_to_mm(T, (u, v)), (u, v))
                  for u, v, _ in ((0, 0, 900), (100, 0, 800), (1000, 0, 60))]
    elegidas = pick_pair(candidatas, [(0, 0, 900), (100, 0, 800),
                                      (1000, 0, 60)])
    assert [p[1] for p in elegidas] == [(0, 0), (100, 0)], elegidas
    print("seleccion de pareja: OK (ignora mota pequena aunque este mas lejos)")

    # El paso se pide en mm y sale en ms: los tres puntos medidos, el recorte a
    # 0.1-10 mm y el minimo de 1 ms (por debajo la Ruida no distingue el pulso).
    assert ms_de_paso(0.1) == ms_de_paso(0.0) >= 1, "0 y 0.1 valen lo mismo"
    assert ms_de_paso(3.4) == STEP_MS[-1], "3.4 mm es un punto medido"
    assert ms_de_paso(0.44) == STEP_MS[1], "0.44 mm es un punto medido"
    assert ms_de_paso(10.0) > ms_de_paso(3.4) > ms_de_paso(0.1), "mas mm, mas ms"
    print("paso mm->ms: OK (puntos medidos, recorte 0.1-10 y minimo de 1 ms)")

    # El mapeo de las flechas falla en silencio: si una tecla no esta
    # reconocida el cabezal no se mueve y no sale ningun error. Por eso se
    # saca el VK de la palabra alta en vez de escribir los numeros enteros, que
    # dependen de como OpenCV los empaquete. Las dos formas conocidas, mas la
    # extendida, y que las letras no se confundan con flechas.
    assert _flecha(0x250000, False) == "-X" and _flecha(0x270000, False) == "+X"
    assert _flecha(0x260000, False) == "+Y" and _flecha(0x280000, False) == "-Y"
    assert _flecha(0x25004B, False) == "-X"        # VK con el scan debajo
    assert _flecha(0x4B, True) == "-X" and _flecha(0x4D, True) == "+X"
    assert _flecha(0x48, True) == "+Y" and _flecha(0x50, True) == "-Y"
    # 0x48 y 0x4B son H y K en mayusculas: sin la pista de ARROW_EXT una letra
    # moveria el cabezal. Y una tecla normal no es flecha.
    assert _flecha(0x48, False) is None and _flecha(0x4B, False) is None
    assert _flecha(ord("H"), False) is None and _flecha(0, False) is None
    # Los scan codes de msvcrt son otros: la izquierda es P y abajo S, no K y P.
    assert MS_SCAN == {0x48: "+Y", 0x50: "-X", 0x4D: "+X", 0x53: "-Y"}, MS_SCAN
    assert SCAN[0x4B] == "-X" and SCAN[0x50] == "-Y" and MS_SCAN[0x50] == "-X"
    assert SCAN[0x50] == "-Y" and MS_SCAN[0x53] == "-Y"
    assert len(STEP_MS) == len(STEP_MM) and STEP_MM[0] < STEP_MM[-1]
    assert _escala(np.zeros((720, 1280, 3), np.uint8), 360).shape[:2] == (360, 640)
    # Todas las formas en las que puede llegar una flecha tienen que mapear a
    # los cuatro ejes, que si no decide el build de OpenCV de cada uno.
    for cod in (0x250000, 0x25004B, 0x25):
        assert _flecha(cod, False) == "-X", hex(cod)      # izquierda
    assert _flecha(0x270000, False) == "+X", "derecha"
    assert _flecha(0x260000, False) == "+Y", "arriba"
    assert _flecha(0x280000, False) == "-Y", "abajo"
    # Teclado numerico con NumLock apagado, que tambien son flechas.
    assert _flecha(0x22, False) == "-X" and _flecha(0x23, False) == "+X"
    assert _flecha(0x21, False) == "+Y" and _flecha(0x24, False) == "-Y"
    assert _flecha(0x4B, True) == "-X" and _flecha(0x50, True) == "-Y"
    print("flechas: OK (WASD, VK en la palabra alta y forma extendida, "
          "4 ejes, pasos 0.2/0.44/3.4 mm)")

    # El circulo rojo del cabezal dibuja con la H invertida, y
    # perspectiveTransform devuelve 2D (hace la division por la tercera
    # coordenada DENTRO), no 3. Dividirla a mano da IndexError. Se comprueba
    # la forma y el valor, que es como se manifesto el fallo.
    Hc = np.array([[0.5, 0.0, 10.0], [0.0, 0.5, 20.0], [0.0, 0.0, 1.0]])
    # Hinv toma mm y devuelve px, que es como se usa con la posicion del
    # cabezal. 80 mm -> 2*(80-10) = 140 px, y 80 mm -> 2*(80-20) = 120 px.
    q = cv2.perspectiveTransform(np.array([[[80.0, 80.0]]], np.float64),
                                 np.linalg.inv(Hc))[0, 0]
    assert q.shape == (2,), ("perspectiveTransform no devuelve 3 coords", q.shape)
    assert abs(q[0] - 140.0) < 1e-6 and abs(q[1] - 120.0) < 1e-6, q
    print("H invertida: OK (2 coordenadas, sin division a mano)")
    test_mirar()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=int, default=DEFAULTS["res"][0])
    ap.add_argument("--height", type=int, default=DEFAULTS["res"][1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("calibrate", help="homografia pixel -> mm")
    c.add_argument("--park", help="X,Y del estacionamiento, p.ej. 20,20")
    c.add_argument("--top", type=int, help="indice de la camara cenital")
    c.add_argument("--points", type=int, default=6)
    c.add_argument("--frames", type=int, default=3)
    c.add_argument("--manual", action="store_true",
                   help="escribir X Y a mano en vez de leerlos de la Ruida")
    c.add_argument("--no-watch", action="store_true",
                   help="no mostrar la camara del cabezal como puntero")
    c.add_argument("--flip-mov", action="store_true",
                   help="dar la vuelta a W y S a mano, solo si la cenital aun "
                        "no tiene homografia y el sentido sale raro")

    f = sub.add_parser("fov", help="medir el campo de vision de la camara del cabezal")
    f.add_argument("--frames", type=int, default=3)

    r = sub.add_parser("run", help="flujo Print and Cut")
    r.add_argument("--marks", type=int, help="numero de marcas (2 por defecto)")
    r.add_argument("--roi", help="ROI en mm de maquina: x0,y0,x1,y1. Acota "
                   "donde buscar marcas cuando hay tags de por medio")
    r.add_argument("--iters", type=int, default=4)
    r.add_argument("--tol", type=float, default=0.1,
                   help="mm de centrado (0.1 es lo que converge: el paso minimo\n                         del jog es 0.2 mm, 0.05 no llega)")
    r.add_argument("--max-step", type=float, default=2.0, help="mm por iteracion")
    r.add_argument("--frames", type=int, default=3)
    r.add_argument("--debug", action="store_true", help="volcar marcas detectadas")
    r.add_argument("--no-move", action="store_true", help="no mover el cabezal")
    r.add_argument("--emit", help="fichero .txt con las coordenadas finales")

    v = sub.add_parser("cams", help="vista dual en vivo")
    v.add_argument("--top", type=int, help="indice de la camara cenital")
    v.add_argument("--head", type=int, help="indice de la camara del cabezal")
    s = sub.add_parser("scan", help="listar todas las camaras y guardar una foto de cada una")
    s.add_argument("--max-idx", type=int, default=8)
    sub.add_parser("test", help="autocomprobado")
    a = ap.parse_args()
    return {"calibrate": cmd_calibrate, "run": cmd_run, "cams": cmd_cams,
            "scan": cmd_scan, "fov": cmd_fov, "test": cmd_test}[a.cmd](a)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        # La q de la calibracion y el Ctrl+C llegan aqui. Cerrar las ventanas
        # antes, o el proceso se queda vivo con la ventana en pantalla.
        cv2.destroyAllWindows()
        print("\ncancelado")
        sys.exit(130)
