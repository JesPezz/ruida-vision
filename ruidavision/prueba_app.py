"""Prueba de humo de la GUI: construye la ventana entera y la cierra.

No toca camaras ni la Ruida (eso necesita la maquina). Lo que comprueba es lo
que se rompe al anadir un widget o un dibujado: estilos, hojas, callbacks,
dibujado de las dos camaras y el clic sobre una foto congelada.

Por ssh la ventana no llega a mapearse (no hay escritorio) y todos los widgets
se quedan en 1x1, asi que las medidas del lienzo se inyectan a mano en vez de
sacarse de la ventana: lo que se prueba es la aritmetica, no Tk.

    py -3 -m ruidavision.prueba_app
"""
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import numpy as np

import ruidavision.app as A


def bombea(app, segundos=1.2):
    """Pump de eventos: hace correr los `after` y la cola del registro."""
    fin = time.time() + segundos
    while time.time() < fin:
        app.update()
        time.sleep(0.03)


class VideoFalso:
    """Sustituto de app.Video: la camara de verdad no se abre en la prueba.

    Se falsea la CLASE, no el metodo `conectar`: el `after` del arranque se
    guarda el metodo ya enlazado, y cambiar el atributo despues no surte
    efecto (la prueba abriria las camaras de verdad).
    """
    instanciados = []

    def __init__(self, cfg, avisar):
        self.parar = threading.Event()
        self.n = 0
        self.top_im = self.head_im = None
        type(self).instanciados.append(self)

    def start(self):
        pass

    def is_alive(self):
        return True

    def join(self, timeout=None):
        pass

    def frame(self):
        return self.n, self.top_im, self.head_im


class MaquinaFalsa:
    """Sustituto de app.Maquina para que los callbacks no abran el puerto real."""

    def __init__(self):
        self.ip = None
        self.mq = None
        self.simulada = SimpleNamespace(
            pan=object(), park=lambda: None, goto=lambda x, y: None,
            pos=lambda timeout=1.0: (250.0, 180.0))

    def get(self):
        return self.simulada

    def cerrar(self):
        if self.mq:
            self.mq.close()
            self.mq = None
        self.ip = None


def hv_bed_temp(app):
    """bed.png y coords.txt falsos, en un temporal, y las rutas de la app
    apuntando a ellos. Devuelve las dos rutas para borrarlas."""
    d = tempfile.mkdtemp()
    bed = os.path.join(d, "bed.png")
    coo = os.path.join(d, "coords.txt")
    import cv2
    g = np.full((480, 640), 250, "uint8")
    cv2.circle(g, (200, 200), 12, 0, -1)         # una mancha
    cv2.circle(g, (400, 300), 12, 0, -1)         # otra
    cv2.imwrite(bed, g)
    open(coo, "w", encoding="utf-8").write("1  12.500, 20.250 mm\n"
                                           "2  60.000, 70.100 mm\n")
    app.__dict__["_bed_real"] = (A.hv.BED_PNG, A.COORDS)
    A.hv.BED_PNG, A.COORDS = bed, coo
    return bed, coo


def main():
    fallos = []
    hechas = []
    log_original = A.LOGF
    log_temporal = tempfile.TemporaryDirectory()
    A.LOGF = os.path.join(log_temporal.name, "app.log")

    def chk(desc, cond):
        hechas.append(desc)
        fallos.append(desc) if not cond else None
        # flush: sin el, un tramo entero del registro se pierde cuando cv2
        # abre su ventana, y un fallo parece no existir.
        print(("  OK  " if cond else "FALLO ") + desc, flush=True)

    with tempfile.TemporaryDirectory() as temporal:
        base = os.path.join(temporal, "proyecto")
        local = os.path.join(temporal, "Local")
        datos_usuario = os.path.join(local, "Ruida Vision")
        os.makedirs(base)
        os.makedirs(datos_usuario)
        with open(os.path.join(datos_usuario, "calib.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        chk("el modo codigo reutiliza el calibrado de la instalacion",
            A._directorio_datos(False, base, local) == datos_usuario)
        chk("sin calibrado instalado el modo codigo usa los datos del proyecto",
            A._directorio_datos(False, base, None) == base)
        chk("el ejecutable instalado mantiene sus datos de usuario",
            A._directorio_datos(True, base, local) == datos_usuario)

    A.Video = VideoFalso
    # La app mira sola si hay version nueva a los 800 ms, sin boton. Falsear
    # `comprobar` ANTES de que ese `after` dispare: si no, la prueba sale a
    # GitHub de verdad. Se comprueba luego que el aviso llego al pie, que es
    # la prueba de que el `after` estaba bien enganchado.
    A.actualizar.comprobar = lambda *a, **k: (False, "sin novedades", None)
    app = A.App()
    app.maq = MaquinaFalsa()
    # save_cfg falso para TODO el recorrido: si una sola prueba se pasa y
    # guarda, escribe una H de mentira en el calib.json de la maquina y la
    # prueba siguiente revienta con Singular matrix. Aki solo se acumula.
    guardado, save_real = {}, A.hv.save_cfg
    A.hv.save_cfg = lambda c: guardado.update(c)
    app.update_idletasks()
    print("ventana %dx%d con %d hojas" % (app.winfo_width(), app.winfo_height(),
                                          len(app.hojas.tabs())))
    chk("se crean las 4 hojas", len(app.hojas.tabs()) == 4)
    # Las camaras se abren solas al arrancar, sin pulsar nada.
    bombea(app, 0.4)
    chk("las camaras se conectan solas al arrancar",
        isinstance(app.video, VideoFalso) and VideoFalso.instanciados)
    bombea(app)
    chk("el registro recibe lineas", app.txt.get("1.0", "end").strip() != "")
    chk("la app mira las actualizaciones al arrancar, sin pulsar nada",
        "sin novedades" in app.lbl_ota.cget("text"))
    reconexiones = []
    conectar_real = app.conectar
    app.conectar = lambda: reconexiones.append(True)
    app.video = None
    app._volvio = True
    app._foto_lista(SimpleNamespace(
        result=lambda: (_ for _ in ()).throw(RuntimeError("captura simulada fallida"))))
    app.conectar = conectar_real
    chk("una captura fallida informa el error y reconecta las camaras",
        reconexiones == [True] and not app._volvio
        and "captura simulada fallida" in app.lbl_cal.cget("text"))

    # Dibujado de las dos camaras con imagen falsa: homografia, caja de viaje,
    # cruz del cabezal y el texto de SIN HOMOGRAFIA.
    cfg = A.hv.load_cfg()
    gris = np.full((1080, 1920), 120, np.uint8)
    app._foto(app.im_top, app._con_top(gris))
    app._foto(app.im_head, app._con_head(np.full((720, 1280), 90, np.uint8)))
    app.update()
    chk("las dos camaras se dibujan", app.im_top.img is not None)
    app.pos, app._ok = (250.0, 180.0), True
    app.cfg = cfg
    chk("el cabezal se pinta con la H real", app._con_top(gris).shape == (1080, 1920, 3))
    sin_h = dict(cfg)
    sin_h.pop("H", None)
    app.cfg = sin_h
    chk("avisa de que no hay homografia",
        app._con_top(gris).shape == (1080, 1920, 3))
    app.cfg = cfg

    # Foto congelada + clic: el pixel tiene que volver DESHECIDO la escala, y
    # con el pie en (ancho/2, alto/2) el pixel REAL es (960, 540).
    f = A.Foto(app)
    clics = []
    f.al_clic = lambda x, y: clics.append((x, y))
    f.foto = np.zeros((1080, 1920, 3), np.uint8)      # lienzo de 1180x600
    f.esc, f.ox, f.oy = A._encuadre(1180, 600, 1920, 1080)
    ev = lambda x, y: type("Ev", (), {"x": x, "y": y})()
    f._clic(ev(590, 300))                              # el centro del lienzo
    x, y = clics[-1]
    chk("el clic en el centro devuelve el pixel central (%.1f, %.1f)" % (x, y),
        abs(x - 960) < 0.5 and abs(y - 540) < 0.5)
    f._clic(ev(590 + 100, 300))
    chk("100 px de lienzo son 100/escena px de foto", abs(clics[-1][0] - x - 100 / f.esc) < 0.5)
    f._clic(ev(-500, -500))
    chk("el clic se queda dentro de la foto", 0 <= clics[-1][0] < 1920
        and 0 <= clics[-1][1] < 1080)
    # El detector dice donde esta el centro de la mancha; el clic se pega ahi si
    # esta cerca. Lejos de toda marca, el clic vale lo que dice el raton.
    f.detectadas = [(x + 12.0, y), (300.0, 900.0)]
    chk("el clic se pega al centro de la marca cercana", f.snap(x + 9, y + 3) == (x + 12.0, y))
    chk("un clic lejos de toda marca se queda donde esta", f.snap(20.0, 20.0) == (20.0, 20.0))
    pixels, outside = A._marcas_calibracion_en_area(
        [(100.0, 100.0, 500), (-1.0, 100.0, 400)],
        np.eye(3, dtype=np.float32))
    chk("calibracion conserva y separa candidatos fuera del area segura",
        pixels == [(100.0, 100.0), (-1.0, 100.0)]
        and outside == [(-1.0, 100.0)])
    import cv2
    test_frame = np.full((200, 200), 250, np.uint8)
    cv2.circle(test_frame, (50, 50), 8, 0, -1)
    cv2.circle(test_frame, (120, 120), 14, 0, -1)
    detector_cfg = dict(app.cfg, H=np.eye(3, dtype=np.float32).tolist(),
                        max_area=2000)
    encontrados_bajos, _, _ = A._detectar_candidatos_calibracion(
        test_frame, detector_cfg, 0, 40)
    encontrados_altos, _, _ = A._detectar_candidatos_calibracion(
        test_frame, detector_cfg, 0, 400)
    chk("los parametros de umbral y area minima ajustan solo el detector de calibracion",
        len(encontrados_bajos) == 2 and len(encontrados_altos) == 1)

    # El sentido de WASD sale de la homografia real, no de una tabla a mano.
    mapa = A.hv._mapa_wasd(cfg, app.cal_res)
    chk("WASD deducido de la H de calib.json (%s)" % mapa,
        set(mapa) == {"w", "a", "s", "d"} and A.hv._op(mapa["d"]) == mapa["a"])

    # Con un Entry con el foco, una tecla NO puede mover el cabezal.
    ent = A.ttk.Entry(app)
    ent.pack()
    ent.focus_force()
    app.update()
    if A.App._escribiendo(app):
        chk("con un campo enfocado la tecla no mueve",
            app._tecla(type("E", (), {"keysym": "w"})()) is None)
    else:
        print("  (sin escritorio la ventana no toma el foco: prueba del foco omitida)")
    ent.destroy()

    # La calibracion exige 4 puntos y avisa de los que no valen.
    app.lbl_cal.configure(text="")
    app.cal_ajusta()
    app.update()
    chk("avisa si faltan puntos", "4 puntos" in app.lbl_cal.cget("text"))

    # FOV: la distancia real se PREGUNTA en un dialogo. Antes se releia de la
    # casilla de al lado, que ya tenia el FOV guardado, y por eso la medida no
    # cuadraba y la segunda vez se comia a si misma. Se falsea save_cfg para no
    # tocar el calib.json de verdad.
    load_real = A.hv.load_cfg
    ask_real = A.simpledialog.askstring
    A.hv.load_cfg = lambda: dict(app.cfg)
    try:
        app.foto.poner(np.zeros((480, 640, 3), "uint8"), [])   # foto fija de 640 px
        w = int(app.foto.foto.shape[1])
        app.fovpts = [(100.0, 100.0), (200.0, 100.0)]      # 100 px de lado
        A.simpledialog.askstring = lambda *a, **k: "40"    # 100 px = 40 mm
        app._fov()
        chk("el FOV se calcula con la distancia preguntada",
            abs(app.cfg["head_fov_mm"] - 0.4 * w) < 1e-6)
        # Y sale solo en Ajustes: si la casilla se queda con el valor viejo, el
        # siguiente Guardar devuelve el FOV a la medida anterior.
        chk("el FOV medido se ve tambien en Ajustes",
            app.campos["head_fov_mm"].get() == "%.2f" % app.cfg["head_fov_mm"])

        antes = app.cfg["head_fov_mm"]
        A.simpledialog.askstring = lambda *a, **k: None        # cancelado
        app._fov()
        chk("cancelar la distancia deja el FOV como estaba",
            app.cfg["head_fov_mm"] == antes)

        app.campos["head_fov_mm"].delete(0, "end")
        app.campos["head_fov_mm"].insert(0, "31,5")

        # Maquina.get() cachea hv.Machine con la config de cuando se creo y
        # solo lo rehace si cambia la IP: por eso un Estacionamiento guardado
        # no movia nada, la maquina se iba a la posicion de antes.
        class CocheViejo:
            cerrado = False

            def close(self):
                type(self).cerrado = True

        app.maq.mq, app.maq.ip = CocheViejo(), cfg["ip"]
        app.guardar_cfg()
        chk("guardar Ajustes suelta el coche viejo de la maquina",
            CocheViejo.cerrado and app.maq.mq is None)
        chk("Ajustes guarda el FOV como numero y no como texto",
            isinstance(guardado.get("head_fov_mm"), float)
            and abs(guardado["head_fov_mm"] - 31.5) < 1e-9)
    finally:
        A.hv.load_cfg = load_real            # save_cfg sigue falso a proposito
        A.simpledialog.askstring = ask_real
    app.cfg = load_real()

    # Quitar el punto que se ha marcado en la tabla, no solo el ultimo. Los
    # reflejos anaden manchas que no son marcas de calibrado, y el punto malo
    # solia caer en medio de la lista, donde "quitar el ultimo" no llegaba.
    app.puntos = [(10.0, 10.0, 0.0, 0.0), (20.0, 20.0, 5.0, 5.0),
                  (30.0, 30.0, 10.0, 10.0)]
    app.numeros = [1, 2, 3]
    app._retabla()
    app.tabla.selection_set(app.tabla.get_children()[1])       # el de en medio
    app.cal_quita()
    chk("se quita el punto marcado de la tabla, no solo el ultimo",
        app.puntos == [(10.0, 10.0, 0.0, 0.0), (30.0, 30.0, 10.0, 10.0)]
        and app.numeros == [1, 3] and len(app.tabla.get_children()) == 2)
    app.cal_quita()                                            # sin seleccion: el ultimo
    chk("sin nada marcado quita el ultimo", app.puntos == [(10.0, 10.0, 0.0, 0.0)])
    app.puntos = []
    app.numeros = []

    # Numerar a mano: con 70 manchas el punto 1 puede ser la fila 40, y sin
    # esto habia que borrar las 69 de golpe. Ademas "quitar" tiene que seguir
    # mirando la FILA, no el numero, o al renumerar borraria el punto falso.
    app.puntos = [(10.0, 10.0, 0.0, 0.0), (20.0, 20.0, 5.0, 5.0),
                  (30.0, 30.0, 10.0, 10.0)]
    app.numeros = [1, 2, 3]
    app._retabla()
    app.tabla.selection_set(app.tabla.get_children()[1])      # el de en medio
    A.simpledialog.askinteger = lambda *a, **k: 1        # el de en medio es el 1
    app.cal_numero()
    # los ids de fila cambian en cada repintado: se leen por posicion
    chk("el punto marcado se renumera a mano", app.numeros == [1, 1, 3]
        and app.tabla.item(app.tabla.get_children()[1], "values")[0] == "1")
    chk("avisa si el numero ya lo tiene otro",
        "tambien" in app.lbl_cal.cget("text"))
    app.tabla.selection_set(app.tabla.get_children()[1])   # ahora SI es el 1
    app.cal_quita()                                        # -> la fila 1, no la 0
    chk("quitar mira la fila, no el numero", app.puntos == [(10.0, 10.0, 0.0, 0.0),
                                                            (30.0, 30.0, 10.0, 10.0)]
        and app.numeros == [1, 3])
    app.puntos = [(1.0, 1.0, 0.0, 0.0), (2.0, 2.0, 1.0, 1.0),
                  (3.0, 3.0, 2.0, 2.0), (4.0, 4.0, 3.0, 3.0)]
    app.numeros = [1, 2, 3, 5]                  # un 5: no es 1,2,3,4
    app._retabla()
    app.cal_ajusta()                                      # numeros sueltos: no ajusta
    chk("no se ajusta con numeros que no son 1,2,3...",
        "Renumerar" in app.lbl_cal.cget("text"))
    dialogs = []
    showinfo_real, showwarning_real, showerror_real = (
        A.messagebox.showinfo, A.messagebox.showwarning, A.messagebox.showerror)
    A.messagebox.showinfo = lambda *a, **k: dialogs.append(("info", a))
    A.messagebox.showwarning = lambda *a, **k: dialogs.append(("warning", a))
    A.messagebox.showerror = lambda *a, **k: dialogs.append(("error", a))
    try:
        app._ajustado(SimpleNamespace(result=lambda: {
            "ok": True, "max_error": 0.2, "mean_error": 0.1,
            "used": 4, "rejected": []}))
        chk("Ajustar H confirma en un aviso que guardo la homografia",
            dialogs[-1][0] == "info"
            and "Homografia guardada" in dialogs[-1][1][1])
        app._ajustado(SimpleNamespace(result=lambda: {
            "ok": True, "max_error": 1.2, "mean_error": 0.8,
            "used": 3, "rejected": [4]}))
        chk("Ajustar H advierte si hay puntos descartados o error alto",
            dialogs[-1][0] == "warning"
            and "Puntos descartados" in dialogs[-1][1][1])

        def ajuste_fallido():
            raise RuntimeError("fallo simulado al ajustar")

        app._ajustado(SimpleNamespace(result=ajuste_fallido))
        chk("Ajustar H muestra un aviso si falla la tarea",
            dialogs[-1][0] == "error"
            and "fallo simulado al ajustar" in dialogs[-1][1][1])
    finally:
        A.messagebox.showinfo = showinfo_real
        A.messagebox.showwarning = showwarning_real
        A.messagebox.showerror = showerror_real
    A.simpledialog.askinteger = lambda *a, **k: 1
    app.cal_reordena()
    chk("renumerar ordena por numero y deja 1,2,3...",
        app.numeros == [1, 2, 3, 4] and [p[:2] for p in app.puntos]
        == [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)])
    app.puntos = []
    app.numeros = []

    # Calibrar tiene que enseñar los TRES visores a la vez: las dos camaras en
    # vivo y la foto congelada con los puntos. Antes solo estaba la congelada y
    # al entrar en la hoja no se veia nada.
    app.video = VideoFalso(app.cfg, app.log)
    # el video entrega frames en GRIS: _con_top/_con_head los pintan en BGR
    app.video.top_im = np.full((480, 640), 120, "uint8")
    app.video.head_im = np.full((480, 640), 90, "uint8")

    def un_frame():
        app._n = -1                        # fuerza a redibujar
        app.update()
        app._pintar()                     # un turno: no encola el siguiente
    app.hojas.select(app.i_cal)
    un_frame()
    vis = app.vivos.get(app.i_cal)
    chk("Calibrar tiene los dos visores en vivo", vis is not None and len(vis) == 2
        and all(getattr(l, "img", None) is not None for l in vis))
    chk("la foto congelada sigue siendo un visor aparte",
        app.foto is not vis[0] and app.foto is not vis[1])
    app.hojas.select(app.i_vivo)
    antes = [getattr(l, "img", None) for l in app.vivos[app.i_vivo]]
    app.hojas.select(app.i_cal)
    un_frame()
    chk("solo se pinta la hoja que se ve",
        antes == [getattr(l, "img", None) for l in app.vivos[app.i_vivo]])
    app.hojas.select(app.i_marcas)
    un_frame()
    chk("Print and Cut muestra el visor en vivo del cabezal (%d x %d, image=%s)"
        % (app.im_marcas_head.winfo_width(), app.im_marcas_head.winfo_height(),
           getattr(app.im_marcas_head, "img", None) is not None),
        app.vivos[app.i_marcas][0] is None
        and getattr(app.im_marcas_head, "img", None) is not None
        and app.im_marcas_head.winfo_width() >= 320
        # El alto sale del reparto de la rejilla (alto=6), no de un numero: un
        # minimo en pixeles solo valia al 100% de DPI y salia con 78. Lo que se
        # persigue es que sea una franja y no una linea, y eso si se comprueba
        # sin depender del escalado.
        and app.im_marcas_head.winfo_height()
        >= app.im_marcas_head.winfo_width() // 5)
    # El reparto de los cuatro: los dos visores en vivo, la foto congelada y la
    # tabla de puntos. Medianamente era un PanedWindow con la izquierda al 75% y
    # la foto encima de las camaras, y el reparto salia como salia. Ahora es
    # una rejilla 2x2 con filas y columnas del mismo peso: los cuatro tienen que
    # medir LO MISMO, no "parecidos".
    app.hojas.select(app.i_cal)
    app.geometry("1200x780")
    app.update()
    celdas = [(w.winfo_width(), w.winfo_height(), w.winfo_class())
              for w in app.celdas_cal]
    anchos = sorted({c[0] for c in celdas})
    altos = sorted({c[1] for c in celdas})
    # 1 px de margen: la rejilla reparte el hueco sobrante en enteros y a una
    # fila le toca uno mas. Lo que se persigue es que no haya una franja
    # ilegible, no el pixel exacto. El suelo tampoco puede ser un numero de
    # pixeles (salen 204 con el escalado de este PC): que no se haya collapsed
    # ninguna celda es lo que se puede comprobar en cualquier DPI.
    chk("los cuatro elementos de Calibrar miden lo mismo %s" % (celdas,),
        anchos[-1] - anchos[0] <= 1 and altos[-1] - altos[0] <= 1
        and min(anchos[0], altos[0]) > 100)

    # Los botones llevan su nombre encima, no un icono. Se probaron los iconos
    # con tooltip y estorbaban: con la ventana estrecha habia que adivinar.
    def todos(w, acc=None):
        acc = [] if acc is None else acc
        for c in w.winfo_children():
            acc.append(c)
            todos(c, acc)
        return acc
    cosas = todos(app)
    bts = [w for w in cosas if w.winfo_class() == "TButton"]
    nombres = [w.cget("text") for w in bts]
    chk("Print and Cut dispone del jog manual compacto",
        all(nombres.count(name) >= 3 for name in ("+Y", "+X", "-Y", "-X")))
    jog_buttons = [w for w in bts if w.cget("style") == "Jog.TButton"]
    jog_tamanos = [(w.cget("text"), w.winfo_width(), w.winfo_height())
                    for w in jog_buttons]
    chk("los botones de jog son compactos %s" % ascii(jog_tamanos),
        len(jog_buttons) >= 10 and max(w.winfo_width() for w in jog_buttons) < 55
        and max(w.winfo_height() for w in jog_buttons) < 30)
    Iconos = set("⌂◉○⏹⊕◫⧉▤⟳◎➜▶▲▼◀✓⌦✕⇅# ")
    chk("los botones llevan el nombre, no un icono",
        len(bts) >= 20 and not hasattr(A, "ToolTip")
        and not hasattr(app, "_icono_camara")
        and not (set(nombres) & Iconos)
        and {"Origen 0,0", "1. Estacionar", "Renumerar", "Ver coords.txt",
             "Guardar ajustes", "Detectar y centrar los 2"} <= set(nombres))
    # "Estacionar" y "Origen 0,0" hacian lo mismo (park es (20,20) y el origen
    # (0,0): 20 mm de diferencia). En Vivo se quito Estacionar; en Calibrar el
    # del paso 1 se sigue llamando "1. Estacionar", asi que el nombre suelto no
    # puede existir en ninguna hoja.
    chk("Estacionar ya no es un boton suelto (Vivo usa Origen 0,0)",
        "Estacionar" not in nombres and "Origen 0,0" in nombres)
    chk("Origen 0,0 usa el viaje nativo habilitado y Estacionar sigue deshabilitado",
        "disabled" not in str(app.btn_origen.state())
        and "disabled" in str(app.btn_cal_park.state()))
    chk("Calibrar ofrece jog direccional, reconexion y ajuste de deteccion",
        all(name in nombres for name in ("Conectar cámaras", "+Y", "-X", "+X", "-Y",
                                          "Aplicar en foto")))
    chk("el boton de buscar actualizaciones ya no existe",
        "Buscar actualizaciones" not in nombres)
    chk("los botones de mover dicen el eje (+Y, +X, -Y, -X)",
        [t for t in nombres if t in ("+Y", "+X", "-Y", "-X")][:4]
        == ["+Y", "+X", "-Y", "-X"]
        and all(nombres.count(t) >= 3 for t in ("+Y", "+X", "-Y", "-X")))
    app._boton_mover()
    chk("Mover avisa de que no hay puntos", app.btn_mover.cget("text") == "Mover"
        and "disabled" in str(app.btn_mover.state()))

    # Ningun boton puede quedar fuera de la ventana: con los iconos cabia todo,
    # y al volver a los nombres los ultimos se salian sin verse. Se mide de
    # verdad, con la ventana ya colocada.
    chopped = {}
    medidos = 0
    for width in (1200, 940):
        app.geometry("%dx780" % width)
        app.update()
        for pos in range(len(app.hojas.tabs())):
            app.hojas.select(pos)
            app.update()
            for w in todos(app):
                if w.winfo_class() != "TButton" or not w.winfo_ismapped():
                    continue
                medidos += 1
                if w.winfo_rootx() - app.winfo_rootx() + w.winfo_width() > app.winfo_width():
                    chopped.setdefault(
                        "%d px %s" % (width, app.hojas.tab(pos, "text").strip()),
                        []).append(w.cget("text"))
    chk("ningun boton se sale de ventanas de 1200/940 px (%d mirados, fuera: %s, ventana %d)"
        % (medidos, chopped or "ninguno", app.winfo_width()),
        not chopped and medidos >= 40)

    # Los `wraplength` fijos (980 px en Marcas) se salian por la derecha al
    # bajar la ventana: el texto se seguia fuera del widget y no se leia el
    # final de la frase. Ahora siguen al ancho de su marco.
    texts = {}
    for width in (1200, 940):
        app.geometry("%dx780" % width)
        app.hojas.select(app.i_marcas)
        app.update()
        for lbl in A.App._etiquetas(app):
            w = int(lbl.cget("wraplength") or 0)
            if w and lbl.master.winfo_width() > 40:
                texts.setdefault(width, []).append(
                    (w, lbl.master.winfo_width()))
    fuera = [p for pares in texts.values() for p in pares if p[0] > p[1]]
    medidos_textos = sum(len(p) for p in texts.values())
    chk("ningun texto con wraplength se sale a 1200/940 px (%d mirados, fuera: %s)"
        % (medidos_textos, fuera or "ninguno"),
        not fuera and medidos_textos >= 4)
    app.geometry("1200x780")
    app.update()

    # Marcas lleva los cinco pasos, el visor, la lista y dos parrafos mas: unos
    # 680 px de alto a 1200 de ancho, y la ventana minima son 620. Sin scroll lo
    # de abajo no se ve y no hay forma de llegar. Pero en un monitor alto el
    # texto cabe entero (en el PC de Windows, 678 px en 700) y entonces no hay
    # nada que comprobar, ni que la ventana se pueda encoger por `geometry`
    # (alli el resize no se aplica). Asi que se mete un separador gigante: el
    # scroll tiene que aparecer y llegar al final en cualquier pantalla.
    ms = app.hoja_marcas
    hoja_antes = app.hojas.index("current")
    app.hojas.select(ms)
    app.update()
    separador = A.ttk.Frame(ms.interior, height=3000)
    separador.pack()
    app.update_idletasks()
    bombea(app, 0.3)
    alto = (ms.lienzo.bbox("all") or (0, 0, 0, 0))[3]
    chk("la hoja de Marcas se desplaza (%d px de texto en %d de alto)"
        % (alto, ms.lienzo.winfo_height()), alto > ms.lienzo.winfo_height())
    ms.lienzo.yview_moveto(1.0)
    app.update()
    chk("se llega al final del texto de Marcas", ms.lienzo.yview()[1] == 1.0)
    separador.pack_forget()
    ms.lienzo.yview_moveto(0.0)
    app.update()
    chk("al cambiar de hoja el foco vuelve a la ventana (teclear no se queda "
        "en un Entry de la hoja que ya no se ve)", not app._escribiendo())
    app.hojas.select(hoja_antes)        # como estaba: las pruebas de abajo
    app.update()                         # cuentan con la hoja que sea
    # `_cambio_hoja` deja el corte del jog pendiente 60 ms (`_suelta` -> after),
    # y mientras ese after no salta `ir_a` dice "suelta primero el control de
    # jog" y no viaja al origen. Es lo que pasa de verdad en la app, asi que
    # aqui se espera igual que en la app.
    bombea(app, 0.2)

    # - y + eligen el paso de toque sin soltar el WASD, en saltos de 0.1 mm.
    chk("el paso de toque es un numero, no un desplegable",
        app.paso_mm == 0.5 and "0.5 mm" in app.lbl_paso.cget("text")
        and not hasattr(app, "cmb")
        and all("0.5" in label.cget("text") for label in app._jog_step_labels))
    app._cambia_paso(-1)
    chk("- baja el paso de 0.1", app.paso_mm == 0.4)
    app._cambia_paso(2)
    chk("+ sube el paso de 0.1", app.paso_mm == 0.6)
    app._cambia_paso(99)
    chk("el paso no se sale del tope de arriba",
        app.paso_mm == A.hv.PASO_MAX)
    app._cambia_paso(-99)
    chk("el paso no se sale del tope de abajo",
        app.paso_mm == A.hv.PASO_MIN)
    app._pon_paso(1.0)
    app._rueda_paso(SimpleNamespace(delta=120, num=None))
    chk("la rueda incrementa rapidamente el paso en 0.5 mm",
        app.paso_mm == 1.5 and all(
            "1.5" in label.cget("text") for label in app._jog_step_labels))
    app._rueda_paso(SimpleNamespace(delta=0, num=5))
    chk("la rueda tambien reduce el paso en 0.5 mm",
        app.paso_mm == 1.0)
    app._pon_paso(0.5)
    app.hojas.select(app.i_vivo)

    # El panel de la Ruida escucha en un puerto fijo: si el run de Marcas lo
    # encuentra abierto, bind() falla con [WinError 10048], el run se cae y no
    # se escribe coords.txt ("sin coordenadas: mira el registro").
    app.maq.mq = SimpleNamespace(close=lambda: None)
    app.maq.ip = app.cfg["ip"]
    app._volvio = bool(app.video)
    app.parar_cams()
    app._tarea = lambda fn, *a, **kw: fn(*a)
    app._run(lambda ns: None, app._ns(marks=1))
    chk("el run suelta el panel del cabezal antes de abrir el suyo",
        app.maq.mq is None)

    # Marcas tiene que ENSENAR lo que ha visto el detector: la hoja se quedaba
    # en negro y no habia forma de saber si habia marcas o no. Se falsean las
    # rutas a un temporal: bed.png y coords.txt de verdad son de la maquina.
    # H identidad y ROI que cubre el bed.png entero (640x480): px == mm y las
    # dos manchas del temporal caen dentro, sean cuales sean los del calib.
    bed, coo = hv_bed_temp(app)
    app.cfg["H"] = np.eye(3, dtype=np.float32).tolist()
    app.v_roi.delete(0, "end")
    app.v_roi.insert(0, "0,0,640,480")
    try:
        frame = A.cv2.imread(bed, A.cv2.IMREAD_GRAYSCALE)
        detected = A.hv.find_marks(
            frame, min_area=app.cfg["min_area"], max_area=app.cfg["max_area"],
            thr=app.cfg["thr"], merge=6)
        expected, _ = A.hv.marcas_utiles(
            detected, np.eye(3, dtype=np.float32), (0, 0, 640, 480))
        expected = A.hv.pick_pair(expected)
        app._fin_marcas(SimpleNamespace(result=lambda: expected))
        chk("al terminar el run se ve la foto de la cama",
            getattr(app.foto_marcas, "foto", None) is not None)
        chk("las coordenadas van a la lista", len(app.lst.get(0, "end")) == 2)
        chk("el visor pinta los mismos pixeles devueltos por la deteccion",
            app.marcas_utiles == [tuple(mark[1]) for mark in expected])
        chk("las coordenadas mostradas corresponden a las marcas seleccionadas",
            app.marcas2 == [tuple(mark[0]) for mark in expected])
        chk("el detector deja las DOS marcas para pintar",
            len(app.foto_marcas.detectadas) == len(expected) == 2)
        chk("las dos marcas tienen reticulas grandes y numeradas",
            len(app.foto_marcas.find_withtag("marca-1")) == 5
            and len(app.foto_marcas.find_withtag("marca-2")) == 5)
        cajas = [app.foto_marcas.bbox("marca-%d" % i) for i in (1, 2)]
        chk("las marcas del visor ocupan ubicaciones distintas",
            all(cajas) and cajas[0] != cajas[1])
    finally:
        A.hv.BED_PNG, A.COORDS = app.__dict__.pop("_bed_real")
        os.unlink(bed)
        os.unlink(coo)

    # El flujo de Print and Cut: detectar (sin mover) deja los dos puntos a la
    # vista, y el boton Mover va al primero y luego al segundo. Nada de copiar
    # y pegar: el usuario lee la posicion del cabezal en pantalla.
    # Se parte del estado "sin detectar" a mano: _fin_marcas acaba de dejarlo
    # con las dos marcas del bed.png falso, y lo que se prueba aqui es el boton
    # apagado cuando NO hay puntos.
    app.marcas2, app.i_marca = [], 0
    app._boton_mover()
    chk("sin detectar no hay puntos que mover", app.marcas2 == []
        and "disabled" in str(app.btn_mover.state()))
    fake = [(1.5, 2.5), (61.0, 41.0)]
    # H identidad: px == mm, y asi las cifras del test son las que se ven.
    app.cfg["H"] = np.eye(3, dtype=np.float32).tolist()
    found = [(1.5, 2.5, 900), (61.0, 41.0, 800), (3.0, 3.0, 80),
             (600, 900, 700), (30, 30, 600)]
    app._tarea = lambda fn, *a, **kw: fn(*a)
    app._ok = True
    app.cfg["cam_offset_mm"] = [0.0, 0.0]
    movimientos = []
    def goto_falso(x, y):
        movimientos.append((x, y))
        return fake[len(movimientos) - 1]

    maquina_falsa = SimpleNamespace(
        pan=object(),
        goto_native=goto_falso,
        pos=lambda: fake[len(movimientos) - 1])
    app.maq = SimpleNamespace(get=lambda: maquina_falsa)
    marcas, aviso = app._elige_marcas(found)
    chk("solo se quedan las manchas dentro del area de trabajo", len(marcas) == 2)
    chk("elige los dos componentes grandes y no el reflejo lejano",
        marcas == [tuple(found[0][:2]), tuple(found[1][:2])])
    chk("las de fuera se dicen, no se esconden",
        aviso != "" and "fuera" in aviso.lower())
    app.marcas2 = marcas
    app._boton_mover()
    chk("Mover habilita el destino nativo al detectar los dos puntos",
        "disabled" not in str(app.btn_mover.state())
        and app.btn_mover.cget("text") == "Mover 1")

    def tarea_inline(fn, *args, al_terminar=None, **kwargs):
        try:
            result = fn(*args, **kwargs)
            error = None
        except Exception as e:
            result, error = None, e
        future = SimpleNamespace(
            result=lambda: (_ for _ in ()).throw(error) if error else result,
            exception=lambda: error)
        if al_terminar:
            al_terminar(future)
        return future

    app._tarea = tarea_inline
    # La vision fina se prueba en hybrid_vision.py test; aqui lo que importa es
    # que Mover mande el destino nativo y avance. Sin camara, _marca_a devuelve
    # un problema, que es justo lo que se comprueba unas lineas mas abajo.
    fine_real = A.hv.fine
    A.hv.fine = lambda pan, foto, cfg, ns: (pan.pos(), None)
    origenes = []
    goto_nativo_anterior = maquina_falsa.goto_native
    maquina_falsa.goto_native = lambda x, y: (
        origenes.append((x, y)) or (0.0, 0.0))
    app._native_move_fault = False
    app.ir_a(0.0, 0.0)
    chk("Origen 0,0 envia el movimiento nativo y confirma llegada",
        origenes == [(0.0, 0.0)] and app._ok
        and "origen confirmado" in app.lbl_pos.cget("text"))
    origen_error = RuntimeError("confirmacion de origen simulada")
    showerror_origen = A.messagebox.showerror
    A.messagebox.showerror = lambda *a, **k: None
    app._fin_origen(SimpleNamespace(
        result=lambda: (_ for _ in ()).throw(origen_error)))
    A.messagebox.showerror = showerror_origen
    chk("Origen sin confirmacion bloquea viajes posteriores",
        app._native_move_fault and "disabled" in str(app.btn_origen.state()))
    app._native_move_fault = False
    app._boton_origen()
    maquina_falsa.goto_native = goto_nativo_anterior
    app.mover_marca()
    chk("Mover 1 envia el primer destino nativo y avanza el flujo",
        movimientos == [tuple(marcas[0])] and app.i_marca == 1
        and app.btn_mover.cget("text") == "Mover 2")
    app.mover_marca()
    chk("Mover 2 envia el segundo destino nativo",
        movimientos == [tuple(marcas[0]), tuple(marcas[1])]
        and app.i_marca == 2)

    # Sin camara, el centrado no se puede hacer. No es un fallo de motor: el
    # punto NO avanza, se avisa y Mover sigue disponible para reintentar.
    A.hv.fine = fine_real
    app.i_marca = 0
    app._native_move_fault = False
    movimientos.clear()
    app.mover_marca()
    chk("sin camara el punto no avanza y no es fallo de motor",
        app.i_marca == 0 and not app._native_move_fault
        and "SIN CENTRAR" in app.lbl_punto.cget("text")
        and "disabled" not in str(app.btn_mover.state()))
    A.hv.fine = lambda pan, foto, cfg, ns: (pan.pos(), None)

    app.cfg["cam_offset_mm"] = [-49.132, 1.438]
    app._fin_punto(0, (390.638, 312.232))
    chk("el punto 1 mantiene la conversion de offset previa",
        "X = 341.506" in app.lbl_punto.cget("text")
        and "Y = 313.670" in app.lbl_punto.cget("text"))
    app._fin_punto(1, (312.730, 312.436))
    chk("el punto 2 mantiene la lectura directa previa",
        "X = 312.730" in app.lbl_punto.cget("text")
        and "Y = 312.436" in app.lbl_punto.cget("text"))
    app.cfg["cam_offset_mm"] = [0.0, 0.0]
    avisos = []
    showwarning_real = A.messagebox.showwarning
    A.messagebox.showwarning = lambda *a, **k: avisos.append(a)
    app._native_move_active = True
    app.stop()
    app._native_move_active = False
    A.messagebox.showwarning = showwarning_real
    # El estado en color es la senal de que el panel y las camaras viven o no.
    # Si el verde/rojo se cae en un texto nuevo, el operador se queda mirando
    # una linea gris pensando que todo va bien.
    app._pos(SimpleNamespace(result=lambda: (12.5, -3.0)))
    app._pos(SimpleNamespace(result=lambda: None))
    app.conectar()
    abrir = (app.lbl_pan.cget("style"), app.lbl_pos.cget("style"),
             app.lbl_cam.cget("style"))
    chk("la cabecera se pone roja cuando el panel no contesta",
        abrir[0] == "Mal.TLabel" and abrir[1] == "Mal.TLabel"
        and abrir[2] == "Chico.TLabel")
    app.parar_cams()                   # cierra el hilo que abre conectar()
    app._pos(SimpleNamespace(result=lambda: (12.5, -3.0)))
    chk("la cabecera se pone verde cuando el panel contesta",
        app.lbl_pan.cget("style") == "Ok.TLabel"
        and app.lbl_pos.cget("style") == "Ok.TLabel")
    chk("Parar advierte que el viaje nativo no se puede cancelar",
        avisos and "no interrumpe" in avisos[0][1].lower())
    chk("centrado automatico tambien queda deshabilitado",
        "disabled" in str(app.btn_run_marcas.state()))
    app.marcas2, app.i_marca = marcas, 0
    errores_ui = []
    showerror_real = A.messagebox.showerror
    A.messagebox.showerror = lambda *a, **k: errores_ui.append(a)
    del app._tarea
    app._tarea(
        lambda: (_ for _ in ()).throw(RuntimeError("fallo simulado de jog")),
        al_terminar=lambda fu: app.call(app._fin_movimiento, 0, fu))
    bombea(app, 0.3)
    A.messagebox.showerror = showerror_real
    registro = open(A.LOGF, encoding="utf-8").read()
    chk("si falla un movimiento Mover no salta al punto 2",
        app.i_marca == 0 and app._native_move_fault
        and "disabled" in str(app.btn_mover.state()))
    chk("el fallo del movimiento se muestra en la pestaña y en un aviso",
        "fallo simulado de jog" in app.lbl_marcas.cget("text")
        and errores_ui and "fallo simulado de jog" in errores_ui[0][1])
    chk("el registro del movimiento se guarda en app.log",
        "fallo simulado de jog" in registro)
    app.marcas2, app.i_marca = [], 0
    app._boton_mover()

    # La descarga de la OTA se armaba con os.path.dirname del nombre del
    # adjunto, que en un nombre suelto es "", dejando una ruta RELATIVA: la
    # descarga fallaba y aun asi se cerraba la app.
    rutas = []
    app._tarea = lambda fn, *a, **kw: fn(*a)
    real = A.actualizar.descargar

    def falsa(url, destino=None):
        rutas.append(destino)
        yield None
    A.actualizar.descargar = falsa
    real_lanzar = A.actualizar.lanzar
    A.actualizar.lanzar = lambda ruta: None
    A.messagebox.askyesno = lambda *a, **k: True
    app._ota_vuelve(type("F", (), {"result": lambda s: (
        True, "hay", {"assets": [{"name": "instalar_RuidaVision_9.9.exe",
                                 "size": 12345, "browser_download_url":
                                 "https://x/instalar_RuidaVision_9.9.exe"}]})})())
    A.actualizar.descargar = real
    A.actualizar.lanzar = real_lanzar
    chk("la descarga de la OTA va a una ruta absoluta en %TEMP%",
        bool(rutas) and os.path.isabs(rutas[0])
        and rutas[0].endswith("instalar_RuidaVision_9.9.exe")
        and os.path.dirname(rutas[0]) == tempfile.gettempdir())

    # Jog: un toque corto da UN paso fino (hold), mantener pasa a continuo
    # (jog_hold) y al soltar se para. Se falsea el panel (para no abrir el
    # socket) y se vuelve sincrono el hilo de trabajo: lo que se prueba es la
    # maquina de estados del jog, no el pool.
    class PanFalso:
        def __init__(self):
            self.holds, self.jogs, self.stops = [], [], 0

        def hold(self, k, ms):
            self.holds.append((k, ms))

        def jog_hold(self, k, ev):
            self.jogs.append((k, ev))

        def resume(self):
            pass

        def stop(self):
            self.stops += 1

    pan = PanFalso()
    app.maq.get = lambda: type("M", (), {"pan": pan})()
    app.video = VideoFalso(app.cfg, app.log)
    app._native_move_fault = False
    app._native_move_active = False
    app._tarea = lambda fn, *a, **kw: fn(*a)
    for attr in ("_jog_d", "_jog_t", "_jog_p", "_jog_ev"):
        setattr(app, attr, None)

    app._toque("+X")                     # toque corto: suelta antes de LARGO_MS
    app._suelta("+X")
    bombea(app, 0.3)
    chk("un toque da un solo paso fino (+X)",
        pan.holds == [("+X", A.hv.ms_de_paso(app.paso_mm))])
    chk("un toque no arranca el continuo", not pan.jogs)

    app._toque("+Y")                     # mantener: pasa a continuo
    bombea(app, 0.4)
    chk("mantener arranca el jog continuo (+Y)",
        len(pan.jogs) == 1 and pan.jogs[0][0] == "+Y")
    ev = pan.jogs[0][1] if pan.jogs else None
    app._suelta("+X")                    # soltar otra tecla no corta el jog de +Y
    chk("soltar otra direccion no corta el jog", ev is not None and not ev.is_set())
    app._suelta("+Y")
    bombea(app, 0.3)
    chk("al soltar se corta el continuo", ev is not None and ev.is_set())

    ev_stop = threading.Event()
    app._jog_ev = ev_stop
    app.stop()                           # liberacion directa, sin esperar al pool
    chk("PARAR detiene el panel inmediatamente", pan.stops == 1)
    chk("PARAR tambien cancela el evento del jog continuo",
        ev_stop.is_set() and app._jog_ev is None)

    # Cambiar de hoja con el WASD pulsado dejaba el cabezal andando: el
    # KeyRelease lo recibia la hoja nueva y el continuo se quedaba vivo.
    app.hojas.select(app.i_vivo)
    app.update()
    app._toque("+X")
    bombea(app, 0.4)
    ev2 = pan.jogs[-1][1] if pan.jogs else None
    app.hojas.select(app.i_cal)          # <- el cambio de hoja
    bombea(app, 0.2)
    chk("cambiar de hoja corta el toque continuo", ev2 is not None and ev2.is_set())

    # El registro es donde se mira cuando algo va mal: con wrap="none" las
    # lineas largas (un error con una ruta, un paso de homografia) se perdian
    # por debajo del borde sin scrollbar, y el texto de fondo lo hace ilegible.
    app.txt.configure(state="normal")
    app.txt.insert("end", "x" * 400 + "\nfin del registro\n")
    app.txt.configure(state="disabled")
    app.update()
    app.txt.yview_moveto(1.0)            # al final, como hace la app
    app.update()
    # Con wrap="none" la linea de 400 caracteres se iba por debajo del borde y
    # no habia scrollbar: lo que no cabia no se veia nunca. Con "word" la
    # ultima linea cae dentro del alto del widget, que es lo que se comprueba.
    y_ultima = app.txt.dlineinfo("end-1c")[1]
    chk("el registro no corta las lineas largas y tiene barra",
        app.txt.cget("wrap") == "word"
        and bool(app.txt.cget("yscrollcommand"))
        and y_ultima is not None and y_ultima <= app.txt.winfo_height())

    app.salir()
    # El hilo del pool sigue vivo un instante (parkado) despues del shutdown:
    # lo que importa es que el pool quede cerrado y el proceso pueda salir, que
    # es lo que se ve al terminar esta prueba sin que se quede colgada.
    chk("el pool de trabajo queda cerrado", app.pool._shutdown is True)

    # El escalado de Windows: en Linux esto no se ejecuta nunca, asi que se
    # falsea `sys.platform` y `ctypes.windll` para recorrer las cuatro ramas.
    # Si el nombre de una API esta mal escrito, aqui no se ve y se rompe en el PC.
    class WindllFalso:
        def __init__(self, **respuestas):
            self.respuestas, self.llamadas = respuestas, []

        def __getattr__(self, dll):
            yo = self

            class Libreria:
                def __getattr__(self, func):
                    def llama(*a):
                        yo.llamadas.append(dll + "." + func)
                        return yo.respuestas.get(dll + "." + func, False)
                    return llama
            return Libreria()

    import ctypes
    plataforma = sys.platform
    sys.platform = "win32"
    try:
        for resps, esperado in (
                ({"user32.SetProcessDpiAwarenessContext": True}, "V2"),
                ({"shcore.SetProcessDpiAwareness": True}, "por monitor"),
                ({"user32.SetProcessDPIAware": True}, "del sistema"),
                ({}, "por defecto")):
            ctypes.windll = WindllFalso(**resps)
            chk("el escalado cae en '%s'" % esperado,
                esperado in A._dpi_awareness())
        ctypes.windll = WindllFalso()
        A._dpi_awareness()
        chk("la primera llamada es la de Windows 10 (PER_MONITOR_AWARE_V2)",
            ctypes.windll.llamadas[0]
            == "user32.SetProcessDpiAwarenessContext")
    finally:
        sys.platform = plataforma
        if hasattr(ctypes, "windll"):
            del ctypes.windll

    A.hv.save_cfg = save_real          # ya nadie puede escribir el calib.json
    A.LOGF = log_original
    log_temporal.cleanup()
    # el total tambien: si el registro se pierde a medias, el numero dice si las
    # comprobaciones llegaron a correr todas.
    print("app: %d fallos de %d comprobaciones %s"
          % (len(fallos), len(hechas), fallos or ""))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
