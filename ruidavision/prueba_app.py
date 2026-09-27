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

    def chk(desc, cond):
        hechas.append(desc)
        fallos.append(desc) if not cond else None
        # flush: sin el, un tramo entero del registro se pierde cuando cv2
        # abre su ventana, y un fallo parece no existir.
        print(("  OK  " if cond else "FALLO ") + desc, flush=True)

    A.Video = VideoFalso
    app = A.App()
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
    # El reparto de los tres visores: los dos en vivo no salen mas pequeños que
    # la foto congelada, que era justo lo que se quejaba.
    app.hojas.select(app.i_cal)
    app.update()
    top, head = app.vivos[app.i_cal]
    v_alto, h_alto = top.winfo_height(), head.winfo_height()
    f_alto = app.foto.winfo_height()
    chk("los visores en vivo ya no son una franja ilegible (%dx%d, foto %dx%d)"
        % (top.winfo_width(), v_alto, app.foto.winfo_width(), f_alto),
        min(v_alto, h_alto) > 80 and v_alto == h_alto)
    chk("la foto congelada ya no se come la hoja", f_alto > 0
        and v_alto / max(f_alto, 1) > 0.7)
    # 5% de margen: la barra de la hoja se come unos pixeles de una columna.
    anchos = sorted((top.winfo_width(), head.winfo_width()))
    chk("los tres visores se reparten el ancho (%d/%d, foto %d)"
        % (anchos[0], anchos[1], app.foto.winfo_width()),
        anchos[1] - anchos[0] <= 0.05 * anchos[1]
        and app.foto.winfo_width() > anchos[1])

    # Los botones llevan icono y leyenda flotante (punto 3).
    def todos(w, acc=None):
        acc = [] if acc is None else acc
        for c in w.winfo_children():
            acc.append(c)
            todos(c, acc)
        return acc
    cosas = todos(app)
    iconos = [w for w in cosas if w.winfo_class() == "TButton"
              and len(w.cget("text")) <= 3]
    tips = [w for w in cosas if isinstance(w, A.ToolTip)]
    chk("los botones de las hojas son iconos con leyenda flotante",
        len(iconos) >= 6 and len(tips) >= len(iconos)
        and all(len(t.cget("text")) > 6 for t in tips))
    t0 = tips[0]
    # La hoja de la leyenda puede estar oculta (esta comprueba el gestor, no
    # el pixel: asi vale para los 30 botones sin desocultar 4 hojas).
    ev = type("E", (), {"x_root": 10, "y_root": 20})()
    t0.mostrar(ev)
    chk("la leyenda no sale hasta que el raton se para",
        t0.winfo_manager() != "place" and t0._t is not None)
    t0._enseguida()
    app.update()
    chk("la leyenda sale al parar el raton encima",
        t0.winfo_manager() == "place")
    t0.mover(ev)
    chk("la leyenda sigue al raton mientras sale", t0.winfo_manager() == "place")
    t0.ocultar()
    app.update()
    chk("la leyenda se va al salir el raton",
        t0.winfo_manager() != "place" and t0._t is None)

    # - y + eligen el paso de toque sin soltar el WASD, en saltos de 0.1 mm.
    chk("el paso de toque es un numero, no un desplegable",
        app.paso_mm == 0.5 and "0.5 mm" in app.lbl_paso.cget("text")
        and not hasattr(app, "cmb"))
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
    bed, coo = hv_bed_temp(app)
    try:
        app._fin_marcas(None)
        chk("al terminar el run se ve la foto de la cama",
            getattr(app.foto_marcas, "foto", None) is not None)
        chk("las coordenadas van a la lista", len(app.lst.get(0, "end")) == 2)
    finally:
        A.hv.BED_PNG, A.COORDS = app.__dict__.pop("_bed_real")
        os.unlink(bed)
        os.unlink(coo)

    # El flujo de Print and Cut: detectar (sin mover) deja los dos puntos a la
    # vista, y el boton Mover va al primero y luego al segundo. Nada de copiar
    # y pegar: el usuario lee la posicion del cabezal en pantalla.
    chk("sin detectar no hay puntos que mover", app.marcas2 == []
        and "disabled" in str(app.btn_mover.state()))
    fake = [(1.5, 2.5), (61.0, 41.0)]
    # H identidad: px == mm, y asi las cifras del test son las que se ven.
    app.cfg["H"] = np.eye(3, dtype=np.float32).tolist()
    found = [(10, 10, 900), (20, 20, 800), (600, 900, 700), (30, 30, 600)]
    app._tarea = lambda fn, *a, **kw: fn(*a)
    app._ok = lambda p: True
    app._marca_a = lambda mm, i: fake[i]                 # sin Ruida de verdad
    marcas, aviso = app._elige_marcas(found)
    chk("solo se quedan las manchas dentro del area de trabajo", len(marcas) == 2)
    chk("las de fuera se dicen, no se esconden",
        aviso != "" and "fuera" in aviso.lower())
    app.marcas2 = marcas
    app._boton_mover()
    chk("el boton ofrece mover al punto 1",
        "Mover 1" in app.btn_mover.cget("text"))
    app.mover_marca()
    app._fin_punto(0, fake[0])
    chk("tras mover a 1 el boton ofrece el punto 2",
        "Mover 2" in app.btn_mover.cget("text"))
    chk("la posicion del punto 1 se lee en pantalla",
        "PUNTO 1" in app.lbl_punto.cget("text")
        and "1.500" in app.lbl_punto.cget("text"))
    app.mover_marca()
    app._fin_punto(1, fake[1])
    chk("la posicion del punto 2 se lee en pantalla",
        "PUNTO 2" in app.lbl_punto.cget("text")
        and "61.000" in app.lbl_punto.cget("text"))
    chk("en el punto 2 se avisa del offset de LightBurn",
        "offset" in app.lbl_aviso_marca.cget("text").lower())
    app._boton_mover()
    chk("acabados los dos puntos no hay mas que mover",
        "2" not in app.btn_mover.cget("text"))
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

        def stop(self):
            self.stops += 1

    pan = PanFalso()
    app.maq.get = lambda: type("M", (), {"pan": pan})()
    app.video = VideoFalso(app.cfg, app.log)
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

    app.stop()                           # el boton PARAR tambien corta el jog
    chk("PARAR detiene el panel", pan.stops == 1)

    app.salir()
    # El hilo del pool sigue vivo un instante (parkado) despues del shutdown:
    # lo que importa es que el pool quede cerrado y el proceso pueda salir, que
    # es lo que se ve al terminar esta prueba sin que se quede colgada.
    chk("el pool de trabajo queda cerrado", app.pool._shutdown is True)
    A.hv.save_cfg = save_real          # ya nadie puede escribir el calib.json
    # el total tambien: si el registro se pierde a medias, el numero dice si las
    # comprobaciones llegaron a correr todas.
    print("app: %d fallos de %d comprobaciones %s"
          % (len(fallos), len(hechas), fallos or ""))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
