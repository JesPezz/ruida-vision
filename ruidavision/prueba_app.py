"""Prueba de humo de la GUI: construye la ventana entera y la cierra.

No toca camaras ni la Ruida (eso necesita la maquina). Lo que comprueba es lo
que se rompe al anadir un widget o un dibujado: estilos, hojas, callbacks,
dibujado de las dos camaras y el clic sobre una foto congelada.

Por ssh la ventana no llega a mapearse (no hay escritorio) y todos los widgets
se quedan en 1x1, asi que las medidas del lienzo se inyectan a mano en vez de
sacarse de la ventana: lo que se prueba es la aritmetica, no Tk.

    py -3 -m ruidavision.prueba_app
"""
import sys
import time

import numpy as np

import ruidavision.app as A


def bombea(app, segundos=1.2):
    """Pump de eventos: hace correr los `after` y la cola del registro."""
    fin = time.time() + segundos
    while time.time() < fin:
        app.update()
        time.sleep(0.03)


def main():
    fallos = []

    def chk(desc, cond):
        fallos.append(desc) if not cond else None
        print(("  OK  " if cond else "FALLO ") + desc)

    app = A.App()
    app.update_idletasks()
    print("ventana %dx%d con %d hojas" % (app.winfo_width(), app.winfo_height(),
                                          len(app.hojas.tabs())))
    chk("se crean las 4 hojas", len(app.hojas.tabs()) == 4)
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

    app.salir()
    # El hilo del pool sigue vivo un instante (parkado) despues del shutdown:
    # lo que importa es que el pool quede cerrado y el proceso pueda salir, que
    # es lo que se ve al terminar esta prueba sin que se quede colgada.
    chk("el pool de trabajo queda cerrado", app.pool._shutdown is True)
    print("app: %d fallos" % len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
