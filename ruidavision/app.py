"""Ruida Vision: app de escritorio del sistema de vision hibrida.

Esta capa NO calcula nada: las camaras, la homografia, las marcas y el
movimiento ya estan escritos y probados en hybrid_vision.py, y aqui se llaman.
Lo unico que anade son pantallas, botones, estado en pantalla y un registro.

Tres reglas que vienen de los problemas ya encontrados, no de caprichos:

1. NUNCA en el hilo de la interfaz. Las camaras van en su propio hilo y la
   Ruida en un unico hilo de trabajo (ThreadPoolExecutor con un hilo). La UI
   solo pinta lo ultimo que le llega, con `after`, y todo lo que llega desde
   un hilo pasa antes por una cola que se vacia en la UI.
2. NUNCA `sleep` ni `cv2.imshow` en la UI: las imagenes van por PIL. Una
   ventana de OpenCV encima de la de Tk se come el foco del teclado, que es
   justo el problema que se llevo dos versiones.
3. Lo que se ve, se guarda: en la calibracion se apunto el pixel del CLIC y
   la posicion que lea el panel en ese instante, no el centro del fotograma.
"""

import contextlib
import os
import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

import hybrid_vision as hv
import ruida
from ruidavision import VERSION
from ruidavision import actualizar

# Donde estan los datos. Instalada, la app vive en Archivos de programa, que
# es de solo lectura para un usuario normal: todo lo que la app ESCRIBE
# (calibrado, registro, coordenadas, fotos) va a los datos del usuario, que es
# donde Windows manda. Sin installing, todo junto a los .py.
CONGELADO = getattr(sys, "frozen", False)
BASE = os.path.dirname(sys.executable) if CONGELADO else hv.HERE
DATOS = os.path.join(os.environ.get("LOCALAPPDATA", BASE), "Ruida Vision") \
    if CONGELADO else BASE


def _datos():
    """Los datos del usuario, con el calibrado inicial copiado del paquete si
    no hay todavia. La copia la hace el instalador; esto es el respaldo para
    alguien que ejecute el .exe suelto."""
    os.makedirs(DATOS, exist_ok=True)
    destino = os.path.join(DATOS, "calib.json")
    if not os.path.exists(destino):
        try:
            import shutil
            shutil.copyfile(hv.CFG, destino)
        except Exception:
            pass
    return DATOS


if CONGELADO:
    _datos()
hv.CFG = os.path.join(DATOS, "calib.json")
hv.BED_PNG = os.path.join(DATOS, "bed.png")
LOGF = os.path.join(DATOS, "app.log")
COORDS = os.path.join(DATOS, "coords.txt")

# El boton de CONECTAR es el que manda: mientras no haya camaras abiertas, lo
# de mover y capturar se dice que no, en vez de reventar con un traceback en
# una ventana que no se ve.
FLECHAS = {"Up": "+Y", "Down": "-Y", "Left": "-X", "Right": "+X"}
SIN_CAM = "camaras: sin abrir (pulsa Conectar)"
LASER = "LASER APAGADO: esto mueve el cabezal, no dispara"


# --------------------------------------------------------------------- registro

class _Tee:
    """Lo que imprimen las funciones de hybrid_vision va al registro de la app.

    El `print` de la logica existente acaba en el panel de texto en vez de en
    una consola que no existe, y en vivo, que es donde se ve el progreso de un
    `run` de un minuto.
    """

    def __init__(self, cb):
        self.cb = cb

    def write(self, s):
        for ln in s.splitlines():
            if ln.strip():
                self.cb(ln)
        return len(s)

    def flush(self):
        pass


class Video(threading.Thread):
    """Hilo de las camaras: publica el ultimo fotograma de cada una.

    Solo el ultimo. Una cola de fotogramas acumula lo que la UI no ha pintado
    todavia (2 fps en vez de 30) y no arregla nada. Al parar suelta las
    camaras, que es lo que permite despues abrirlas para una captura puntual.
    """

    def __init__(self, cfg, avisar):
        super().__init__(daemon=True)
        self.cfg, self.avisar = cfg, avisar
        self.parar = threading.Event()
        self.candado = threading.Lock()
        self.n = 0
        self.top_im = self.head_im = None

    def frame(self):
        with self.candado:
            return self.n, self.top_im, self.head_im

    def run(self):
        caps = {}
        try:
            for which in ("top", "head"):
                caps[which] = hv.open_cam(self.cfg, which)
            while not self.parar.is_set():
                f1 = hv.read_gris(caps["top"])
                f2 = hv.read_gris(caps["head"])
                with self.candado:
                    self.top_im, self.head_im, self.n = f1, f2, self.n + 1
        except Exception as e:
            self.avisar("camaras: %s" % e)
        finally:
            for c in caps.values():
                c.release()


class Maquina:
    """El panel de la Ruida, creado una vez y solo desde el hilo de trabajo.

    El socket va ligado a un puerto fijo (50207): dos a la vez se pisan. Con un
    unico hilo de trabajo no hay problema, y por eso el socket no se abre
    nunca en la UI.
    """

    def __init__(self):
        self.ip = None
        self.mq = None

    def get(self):
        ip = hv.load_cfg()["ip"]
        if self.mq is None or ip != self.ip:
            self.cerrar()
            self.mq, self.ip = hv.Machine(hv.load_cfg()), ip
        return self.mq

    def cerrar(self):
        if self.mq:
            self.mq.close()
            self.mq, self.ip = None, None


# ----------------------------------------------------------------- lienzo/foto

def _encuadre(W, H, w, h):
    """Escala y esquina del lienzo: la foto entra entera y centrada.

    Fuera de la clase a proposito: es aritmetica pura y se puede comprobar sin
    ventana (por ssh la ventana no llega a mapearse y no hay ni un pixel que
    medir, asi que probarlo por dentro no probaria nada).
    """
    e = min(W / float(w), H / float(h))
    return e, (W - w * e) / 2.0, (H - h * e) / 2.0


class Foto(tk.Canvas):
    """Un fotograma congelado, con clic que devuelve el pixel REAL.

    Al clicar deshace la escala del dibujo, y con eso el pixel que se guarda es
    el de la foto, que es el que tiene la escala de la homografia.
    """

    def __init__(self, padre, al_clic=None):
        super().__init__(padre, bg="#0d0d0d", height=320, highlightthickness=0)
        self.al_clic, self.cruz = al_clic, None
        self.foto = None
        self.detectadas = []
        self.esc = 1.0
        self.ox = self.oy = 0
        self.id = None
        self.bind("<Configure>", lambda e: self._dibujar())
        self.bind("<Button-1>", self._clic)

    def poner(self, bgr, detectadas=()):
        self.foto, self.cruz, self.id = bgr, None, None
        self.detectadas = list(detectadas)
        self._dibujar()

    def _dibujar(self):
        if self.foto is None:
            return
        h, w = self.foto.shape[:2]
        W, H = max(1, self.winfo_width()), max(1, self.winfo_height())
        if self.id is not None and (W, H) == getattr(self, "medida", None):
            return
        self.medida = (W, H)
        self.esc, self.ox, self.oy = _encuadre(W, H, w, h)
        dw, dh = max(1, int(w * self.esc)), max(1, int(h * self.esc))
        self.id = self.create_image(self.ox, self.oy, anchor="nw",
                                    image=self._imagen(dw, dh))
        self._pintar_cruz()
        self._pintar_marcas()

    def _imagen(self, w, h):
        rgb = cv2.cvtColor(self.foto, cv2.COLOR_BGR2RGB)
        self.img = ImageTk.PhotoImage(Image.fromarray(rgb).resize((w, h)))
        return self.img

    def _pintar_cruz(self):
        self.delete("cruz")
        if not self.cruz:
            return
        x, y = self.cruz[0] * self.esc + self.ox, self.cruz[1] * self.esc + self.oy
        for x1, y1, x2, y2 in ((x - 14, y, x + 14, y), (x, y - 14, x, y + 14)):
            self.create_line(x1, y1, x2, y2, fill="#00ff88", width=2, tags="cruz")

    def _pintar_marcas(self):
        """Lo que ve el detector, dibujado encima de la foto. Sin esto no hay
        manera de saber si una mancha es una marca o ruido antes de clicar."""
        self.delete("marcas")
        for i, (u, v) in enumerate(self.detectadas, 1):
            x, y = u * self.esc + self.ox, v * self.esc + self.oy
            r = max(6.0, 5 * self.esc)
            self.create_oval(x - r, y - r, x + r, y + r, outline="#00ff88",
                             width=2, tags="marcas")
            self.create_text(x + r + 2, y - r, text=str(i), fill="#00ff88",
                             anchor="sw", font=("Segoe UI", 9, "bold"),
                             tags="marcas")

    def snap(self, x, y, tol=18.0):
        """El pixel del clic, corregido al centro de la marca detectada mas
        cercana si esta pegada: el detector lo situa a subpixel, el ojo no."""
        d = [((x - u) ** 2 + (y - v) ** 2, u, v) for u, v in self.detectadas]
        if d:
            dist, u, v = min(d)
            if dist <= tol * tol:
                return u, v
        return x, y

    def pixel(self, cx, cy):
        """Pixel REAL de la foto bajo un clic del lienzo."""
        return (cx - self.ox) / self.esc, (cy - self.oy) / self.esc

    def _clic(self, e):
        if self.al_clic is None or self.foto is None:
            return
        x, y = self.pixel(e.x, e.y)
        h, w = self.foto.shape[:2]
        x, y = min(max(0.0, x), w - 1.0), min(max(0.0, y), h - 1.0)
        self.cruz = (x, y)
        self._pintar_cruz()
        self.al_clic(x, y)


# --------------------------------------------------------------------- la app

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ruida Vision %s" % VERSION)
        self.geometry("1200x780")
        self.minsize(940, 620)
        self.cfg = hv.load_cfg()
        self.lineas = queue.Queue()      # texto del registro
        self.trabajos = queue.Queue()    # cosas que se ejecutan en la UI
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maquina")
        self.maq = Maquina()
        self.video = None
        self.puntos = []                 # [(px, py, X mm, Y mm)]
        self.fovpts = []
        self.modo = None                 # None | "fov"
        self.coords = []
        self._volvio = False
        self._n = 0
        self._pid = None
        self._ok = None
        self.pos = None
        self._rep = None

        self._estilo()
        self._cabecera()
        self.hojas = ttk.Notebook(self)
        self.hojas.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self._hoja_vivo()
        self._hoja_calibrar()
        self._hoja_marcas()
        self._hoja_ajustes()
        self._pie()
        self.protocol("WM_DELETE_WINDOW", self.salir)
        self.bind("<KeyPress>", self._tecla)
        self.after(33, self._pintar)
        self.after(120, self._desdovar)
        self.log("App %s. Datos en %s. %s" % (VERSION, DATOS, LASER))

    # -- aspecto
    def _estilo(self):
        s = ttk.Style(self)
        for t in ("vista", "winnative", "clam"):
            if t in s.theme_names():
                s.theme_use(t)
                break
        s.configure(".", font=("Segoe UI", 10))
        s.configure("TButton", padding=(10, 6))
        s.configure("Cabeza.TLabel", font=("Segoe UI", 15, "bold"))
        s.configure("Aviso.TLabel", foreground="#b35c00", font=("Segoe UI", 10, "bold"))
        s.configure("Ok.TLabel", foreground="#0a7d33")
        s.configure("Mal.TLabel", foreground="#b00020")
        s.configure("Chico.TLabel", foreground="#666666")
        s.configure("Treeview", rowheight=24)
        s.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))

    def _cabecera(self):
        f = ttk.Frame(self, padding=(10, 8))
        f.pack(fill="x")
        ttk.Label(f, text="Ruida Vision %s" % VERSION, style="Cabeza.TLabel").pack(side="left")
        ttk.Label(f, text=LASER, style="Aviso.TLabel").pack(side="left", padx=18)
        self.lbl_pan = ttk.Label(f, text="panel: sin comprobar", style="Chico.TLabel")
        self.lbl_pan.pack(side="right")
        self.lbl_cam = ttk.Label(f, text=SIN_CAM, style="Chico.TLabel")
        self.lbl_cam.pack(side="right", padx=18)

    def _pie(self):
        self.txt = tk.Text(self, height=7, bg="#101418", fg="#cfd8dc",
                           font=("Consolas", 9), wrap="none", state="disabled")
        self.txt.pack(fill="x", side="bottom", padx=8, pady=(4, 0))
        f = ttk.Frame(self, padding=(10, 6))
        f.pack(fill="x", side="bottom")
        self.lbl_ota = ttk.Label(f, text="", style="Chico.TLabel")
        self.lbl_ota.pack(side="left")
        ttk.Button(f, text="Buscar actualizaciones", command=self.ota).pack(side="right")
        ttk.Button(f, text="Abrir registro", command=self.abrir_log).pack(side="right", padx=6)

    # -------------------------------------------------------------- fontaneria
    def log(self, txt):
        """A hilo seguro: el registro es una cola y lo vacia la UI."""
        self.lineas.put(str(txt))

    def call(self, fn, *a):
        """Ejecutar `fn` en el hilo de la UI. Lo unico que se puede tocar
        desde el hilo de trabajo: llamar a un widget de Tk desde ahi parte la
        aplicacion."""
        self.trabajos.put((fn, a))

    def _desdovar(self):
        while True:
            try:
                ln = self.lineas.get_nowait()
            except queue.Empty:
                break
            self.txt.configure(state="normal")
            self.txt.insert("end", ln + "\n")
            self.txt.see("end")
            self.txt.configure(state="disabled")
        while True:
            try:
                fn, a = self.trabajos.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*a)
            except Exception as e:
                self.log("ERROR en la interfaz: %s" % e)
        self.after(120, self._desdovar)

    def _tarea(self, fn, *a, al_terminar=None, **kw):
        """Lanza `fn` en el hilo de trabajo. La UI no espera: sigue pintando.

        `al_terminar` se ejecuta en la UI, que es lo unico donde se puede
        tocar un widget. Un boton que se quedara esperando al hilo es el
        boton que parece colgado, que es el sintoma que se reporto.
        """
        def trabajo():
            try:
                with contextlib.redirect_stdout(_Tee(self.log)):
                    return fn(*a, **kw)
            except Exception as e:
                self.log("ERROR: %s" % e)
                return None
        f = self.pool.submit(trabajo)
        if al_terminar:
            f.add_done_callback(lambda fu: self.call(al_terminar, fu))
        return f

    # ------------------------------------------------------------ hoja "vivo"
    def _hoja_vivo(self):
        h = ttk.Frame(self.hojas, padding=8)
        self.hojas.add(h, text="  Vivo  ")
        b = ttk.Frame(h)
        b.pack(fill="x")
        for txt, cmd in (("Conectar camaras", self.conectar), ("Desconectar", self.parar_cams),
                         ("Estacionamiento", self.park), ("Parar motor", self.stop),
                         ("Origen (0,0)", lambda: self.ir_a(0.0, 0.0))):
            ttk.Button(b, text=txt, command=cmd).pack(side="left", padx=3)
        ttk.Label(b, text="paso:").pack(side="left", padx=(18, 3))
        self.cmb = ttk.Combobox(b, state="readonly", width=8,
                                values=["%.2f mm" % v for v in hv.STEP_MM])
        self.cmb.current(1)
        self.cmb.pack(side="left")
        self.lbl_dir = ttk.Label(b, text="", style="Chico.TLabel")
        self.lbl_dir.pack(side="left", padx=18)

        self.lbl_pos = ttk.Label(h, text="posicion: -", font=("Consolas", 11))
        self.lbl_pos.pack(anchor="w", pady=(6, 0))

        cuerpo = ttk.Frame(h)
        cuerpo.pack(fill="both", expand=True, pady=6)
        self.im_top = self._marco(cuerpo, "CENITAL")
        self.im_head = self._marco(cuerpo, "CABEZAL (microscopio)")

        m = ttk.Frame(h)
        m.pack(fill="x")
        ttk.Label(m, text="Mover:", style="Chico.TLabel").pack(side="left", padx=(0, 8))
        for txt, d in (("W  +Y", "+Y"), ("D  +X", "+X"), ("S  -Y", "-Y"), ("A  -X", "-X")):
            b2 = ttk.Button(m, text=txt, width=6)
            b2.bind("<ButtonPress-1>", lambda e, d=d: self.pulso(d))
            b2.bind("<ButtonRelease-1>", lambda e: self._soltar())
            b2.pack(side="left", padx=2)

    def _marco(self, padre, titulo):
        f = ttk.LabelFrame(padre, text=titulo, padding=4)
        f.pack(side="left", fill="both", expand=True, padx=4)
        lab = tk.Label(f, bg="#0d0d0d", text="sin imagen", anchor="center",
                       font=("Consolas", 9), height=10, width=42)
        lab.pack(fill="both", expand=True)
        return lab

    # -- camaras
    def conectar(self):
        if self.video:
            return
        self.lbl_cam.configure(text="camaras: abriendo...")
        self.video = Video(self.cfg, self.log)
        self.video.start()

    def parar_cams(self):
        v, self.video = self.video, None
        if v:
            v.parar.set()
            v.join(timeout=3)
        self.lbl_cam.configure(text=SIN_CAM, style="Chico.TLabel")

    def _estado_cams(self, n, top, head):
        if top is not None:
            self.lbl_cam.configure(text="camaras: cenital %d, cabezal %d"
                                   % (self.cfg["top_cam"], self.cfg["head_cam"]),
                                   style="Ok.TLabel")
        elif self.video and not self.video.is_alive():
            self.video = None
            self.lbl_cam.configure(text=SIN_CAM, style="Mal.TLabel")

    @property
    def cal_res(self):
        """Resolucion con la que se calibro. La misma que usa mirar(): la de
        la cenital, que es la camara de la que sale la homografia."""
        return tuple(self.cfg.get("top_res") or self.cfg["res"])

    def sentido(self):
        """Que letra va a que eje, deducido de la homografia, en pantalla.

        El sintoma "va a la inversa" hecho visible: si W no apunta al +Y de la
        maquina se ve en el boton, sin tener que probarlo con el cabezal.
        """
        m = hv._mapa_wasd(self.cfg, self.cal_res)
        self.lbl_dir.configure(text="WASD segun la homografia:  " + "   ".join(
            "%s -> %s" % (k.upper(), m[k]) for k in ("w", "a", "s", "d")))

    # -- jogging: un pulso deadbeat por pulsacion, repetido mientras se mantenga
    def pulso(self, d, cada=None):
        if not self.video:
            self.log(SIN_CAM)
            return
        self._tarea(self.maq.get().pan.hold, d, hv.STEP_MS[self.cmb.current()])
        if cada:
            self._rep = self.after(cada, self.pulso, d, cada)

    def _soltar(self):
        if self._rep:
            self.after_cancel(self._rep)
            self._rep = None

    def _tecla(self, e):
        """Solo con la ventana enfocada, y sin autorrepeticion del sistema: un
        pulso por segundo no sirve para mover. Las flechas van por su tabla
        (el teclado, no como este montada la camara) y las letras por la
        homografia, igual que en la linea de ordenes."""
        if self._escribiendo():
            return                      # se esta escribiendo: no muevas nada
        d = FLECHAS.get(e.keysym)
        if not d:
            m = hv._mapa_wasd(self.cfg, self.cal_res)
            d = m.get(e.keysym.lower())
        if d:
            self.pulso(d)
            return "break"

    def _escribiendo(self):
        """Con el foco en un campo, teclear es escribir, no mover el cabezal."""
        return isinstance(self.focus_get(), (ttk.Entry, tk.Entry))

    def park(self):
        self._tarea(self.maq.get().park)

    def stop(self):
        self._tarea(self.maq.get().pan.stop)

    def ir_a(self, x, y):
        self._tarea(self.maq.get().goto, x, y)

    # ------------------------------------------------------ hoja "calibrar"
    def _hoja_calibrar(self):
        self.prompt = ""
        h = ttk.Frame(self.hojas, padding=8)
        self.hojas.add(h, text="  Calibrar  ")
        b = ttk.Frame(h)
        b.pack(fill="x")
        for txt, cmd in (("1 Estacionamiento", self.cal_park),
                         ("2 Congelar foto", self.cal_foto),
                         ("Ajustar y guardar", self.cal_ajusta),
                         ("Quitar el ultimo", self.cal_quita),
                         ("Borrar puntos", self.cal_limpia)):
            ttk.Button(b, text=txt, command=cmd).pack(side="left", padx=3)
        ttk.Label(b, text="  el clic en la foto guarda el punto solo",
                  style="Chico.TLabel").pack(side="left", padx=8)
        ttk.Label(b, text="  FOV cabezal:", style="Chico.TLabel").pack(side="left", padx=(20, 3))
        ttk.Button(b, text="medir", command=self.fov_foto).pack(side="left")
        self.ent_fov = ttk.Entry(b, width=9)
        self.ent_fov.insert(0, "%.2f" % self.cfg["head_fov_mm"])
        self.ent_fov.pack(side="left", padx=4)
        ttk.Label(b, text="mm (lo que miden los dos clics)", style="Chico.TLabel").pack(side="left")
        ttk.Button(b, text="Offset laser", command=self.off_pregun).pack(side="left", padx=(24, 3))

        cuerpo = ttk.PanedWindow(h, orient="horizontal")
        cuerpo.pack(fill="both", expand=True, pady=6)
        self.foto = Foto(cuerpo, self._clic)
        cuerpo.add(self.foto, weight=3)
        d = ttk.Frame(cuerpo, padding=6)
        cuerpo.add(d, weight=1)
        ttk.Label(d, text="Puntos:  pixel de la cenital = maquina",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.tabla = ttk.Treeview(d, columns=("n", "px", "py", "x", "y"), show="headings",
                                  height=16)
        for c, t, w in (("n", "n", 32), ("px", "px", 58), ("py", "py", 58),
                        ("x", "X mm", 78), ("y", "Y mm", 78)):
            self.tabla.heading(c, text=t)
            self.tabla.column(c, width=w, anchor="center")
        self.tabla.pack(fill="both", expand=True)
        self.lbl_cal = ttk.Label(d, text="", style="Chico.TLabel", wraplength=320,
                                 justify="left")
        self.lbl_cal.pack(anchor="w", pady=6)

    def cal_park(self):
        self._tarea(self.maq.get().park)
        self.log("estacionamiento en %s" % (self.cfg["park"],))

    def _congelar(self, which, n=3):
        """Foto parada de una camara. El hilo de video se para antes (en la UI,
        no aqui dentro: tocar un widget desde el hilo de trabajo parte la app):
        dos VideoCapture sobre el mismo indice se reparten los fotogramas."""
        self._volvio = bool(self.video)
        self.parar_cams()

        def f():
            cap = hv.open_cam(self.cfg, which)
            try:
                g = hv.grab(cap, n)
                # Marcas SOLO en la cenital: lo que ve el detector encima de la
                # foto es lo unico que dice que una mancha es una marca. En la
                # camara del cabezal no significaria nada.
                d = (hv.find_marks(g, min_area=self.cfg["min_area"],
                                   max_area=self.cfg["max_area"],
                                   thr=self.cfg["thr"], merge=6)
                     if which == "top" else [])
                return g, [(u, v) for u, v, _ in d]
            finally:
                cap.release()
        return self._tarea(f, al_terminar=self._foto_lista)

    def cal_foto(self):
        self.modo = None
        self.prompt = "Foto congelada. Pon el cabezal ENCIMA de una marca y pulsa 3."
        self._congelar("top")

    def fov_foto(self):
        self.fovpts, self.modo = [], "fov"
        self.prompt = ("FOV: haz clic en los DOS extremos de una distancia que sepas "
                       "de verdad, y pon arriba los mm que hay entre ellos.")
        self._congelar("head")

    def _foto_lista(self, fu):
        g, d = fu.result()
        if g is not None:
            self.foto.poner(g, d)
            # El recuento va debajo del aviso: es la respuesta a "las ha visto o
            # no". Las de la camara del cabezal no son marcas, asi que ahi solo
            # se cuentan como referencia de que la foto tiene contraste.
            self.lbl_cal.configure(
                text="%s\n%d manchas en verde%s"
                % (self.prompt, len(d),
                   "  (el clic se pega al centro de la mas cercana)"
                   if self.modo != "fov" else "  (informativas, no son marcas)"),
                style="Chico.TLabel")
        if self._volvio and not self.video:
            self.conectar()
        self.sentido()

    def _clic(self, x, y):
        if self.modo == "fov":
            self.fovpts.append((x, y))
            self.lbl_cal.configure(text="%d de 2 clics. Distancia en mm:"
                                   % len(self.fovpts), style="Chico.TLabel")
            if len(self.fovpts) == 2:
                self._fov()
            return
        x, y = self.foto.snap(x, y)
        # La posicion se lee en el hilo de trabajo, en el instante del clic: es
        # el pixel del clic CONTRA la posicion de ahi. Guardar la posicion de
        # despues es lo que hacia que los puntos no cuadrasen entre si.
        self._tarea(lambda: (x, y, self.maq.get().pos(2.0)), al_terminar=self._punto)

    def _punto(self, fu):
        r = fu.result()
        if not r or r[2] is None:
            self.lbl_cal.configure(text="el panel no dio posicion. Revisa la IP en "
                                       "Ajustes.", style="Mal.TLabel")
            return
        x, y, p = r
        self.puntos.append((x, y, p[0], p[1]))
        self._retabla()
        self.lbl_cal.configure(text="Punto %d:  pixel (%.0f, %.0f) = maquina "
                                   "(%.3f, %.3f). Repite en otro sitio de la cama."
                                   % (len(self.puntos), x, y, p[0], p[1]))

    def cal_ajusta(self):
        if len(self.puntos) < 4:
            self.lbl_cal.configure(text="hacen falta 4 puntos como minimo, y 6 mejor",
                                   style="Mal.TLabel")
            return

        def f():
            px = [p[:2] for p in self.puntos]
            mm = [p[2:] for p in self.puntos]
            H, e_max, e_avg, malos = hv.fit_homography(px, mm)
            if H is None:
                return "No queda modelo: puntos mas separados y clic en el centro de la mancha."
            cfg = hv.load_cfg()
            cfg["H"] = H.tolist()
            # Los que RANSAC rechazo no se guardan: si se guardaran, la
            # siguiente calibracion los volveria a meter.
            cfg["points"] = [[list(px[i]), list(mm[i])]
                             for i in range(len(px)) if i not in malos]
            hv.save_cfg(cfg)
            return ("Error de reproyeccion sobre %d de %d puntos:  max %.3f mm,  "
                    "medio %.3f mm.  Homografia guardada."
                    % (len(cfg["points"]), len(px), e_max, e_avg))
        self._tarea(f, al_terminar=self._ajustado)

    def _ajustado(self, fu):
        r = fu.result()
        if isinstance(r, str):
            self.cfg = hv.load_cfg()
            self.sentido()
            self.lbl_cal.configure(text=r, style="Ok.TLabel" if not r.startswith("No")
                                   else "Mal.TLabel")
        elif r is None:
            self.lbl_cal.configure(text="fallo al ajustar, mira el registro",
                                   style="Mal.TLabel")

    def _retabla(self):
        self.tabla.delete(*self.tabla.get_children())
        for i, (px, py, mx, my) in enumerate(self.puntos, 1):
            self.tabla.insert("", "end", values=("%d" % i, "%.0f" % px, "%.0f" % py,
                                                 "%.3f" % mx, "%.3f" % my))

    def cal_limpia(self):
        self.puntos = []
        self._retabla()
        self.lbl_cal.configure(text="", style="Chico.TLabel")

    def cal_quita(self):
        """Un clic en la mancha de al lado no se puede quitar de otro modo: hay
        que rehacer los seis puntos por un solo error."""
        if self.puntos:
            self.puntos.pop()
        self._retabla()
        self.lbl_cal.configure(text="quedan %d puntos" % len(self.puntos),
                               style="Chico.TLabel")

    def off_pregun(self):
        """Offset entre la camara del cabezal y el laser.

        `fine` converge el eje de la CAMARA sobre la marca, y esa es la
        posicion que se manda a LightBurn. Si el laser no esta en el mismo sitio
        que el ojo, el laser cae a `d` de la marca y el Print and Cut no
        registra. La correccion no se deduce de nada: se mide con un disparo.

        Pl = Pc + d, y lo que hay que sumar es -d. Midiendo: se dispara en A, el
        punto queda en A + d, y al centrarlo con la camara el panel marca P =
        A + d. De ahi -d = A - P, que es lo que se guarda."""
        def pedir(txt):
            v = messagebox.askstring("Offset laser", txt, parent=self)
            if v is None:
                return None
            try:
                n = [float(x) for x in v.replace(";", ",").split(",") if x.strip()]
            except ValueError:
                n = []
            if len(n) != 2:
                messagebox.showerror("Offset laser", "Hacen falta dos numeros, X e Y: %r"
                                     % v, parent=self)
                return None
            return n

        A = pedir("1) Coordenada del DISPARO (la que le diste al laser), X Y:")
        if A is None:
            return
        P = pedir("2) Coordenada con el punto ya CENTRADO en el cabezal, X Y:")
        if P is None:
            return
        dx, dy = A[0] - P[0], A[1] - P[1]
        cfg = hv.load_cfg()
        cfg["cam_offset_mm"] = [dx, dy]
        hv.save_cfg(cfg)
        self.cfg = cfg
        e = self.campos.get("cam_offset_mm")
        if e is not None:
            e.delete(0, "end")
            e.insert(0, "%.3f; %.3f" % (dx, dy))
        messagebox.showinfo(
            "Offset laser",
            "cam_offset_mm = %.3f, %.3f\n(camara menos laser)\n\n"
            "Comprobalo con un corte de prueba: si el laser se queda al otro lado "
            "de la marca, el signo esta invertido." % (dx, dy), parent=self)

    def _fov(self):
        (x1, y1), (x2, y2) = self.fovpts
        try:
            d = float(self.ent_fov.get().strip().replace(",", "."))
        except ValueError:
            d = 0.0
        h, w = self.foto.foto.shape[:2]
        if d <= 0 or (x1, y1) == (x2, y2):
            self.lbl_cal.configure(text="distancia no valida: pon los mm arriba",
                                   style="Mal.TLabel")
            return
        k = d / max(1e-6, ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)
        cfg = hv.load_cfg()
        cfg["head_fov_mm"] = k * w
        hv.save_cfg(cfg)
        self.ent_fov.delete(0, "end")
        self.ent_fov.insert(0, "%.2f" % cfg["head_fov_mm"])
        self.cfg = cfg
        self.lbl_cal.configure(text="head_fov_mm = %.2f  (%.4f mm/px).  Vertical: %.2f mm"
                                % (cfg["head_fov_mm"], k, k * h), style="Ok.TLabel")

    # --------------------------------------------------------- hoja "marcas"
    def _hoja_marcas(self):
        h = ttk.Frame(self.hojas, padding=8)
        self.hojas.add(h, text="  Marcas (Print and Cut)  ")
        b = ttk.Frame(h)
        b.pack(fill="x")
        ttk.Button(b, text="Ejecutar", command=self.run_marcas).pack(side="left")
        ttk.Button(b, text="Solo detectar, sin mover", command=self.run_solo).pack(side="left", padx=4)
        self.v_marcas = self._ent(b, "marcas", 2)
        self.v_it = self._ent(b, "iteraciones", 4)
        self.v_tol = self._ent(b, "tolerancia mm", "0.1")
        ttk.Label(b, text="  ROI mm x0,y0,x1,y1:").pack(side="left", padx=(16, 3))
        self.v_roi = ttk.Entry(b, width=24)
        self.v_roi.pack(side="left", padx=4)
        ttk.Button(b, text="Abrir coords.txt", command=self.abrir_coords).pack(side="right")
        ttk.Button(b, text="Copiar todo", command=self.copiar).pack(side="right", padx=4)
        self.lst = tk.Listbox(h, font=("Consolas", 11), height=20, bg="#101418",
                              fg="#cfd8dc", activestyle="none")
        self.lst.pack(fill="both", expand=True, pady=6)
        self.lst.bind("<<ListboxSelect>>", self._copia_una)
        ttk.Label(h, text="Clic en una linea = copiarla sola. En LightBurn, Print and "
                          "Cut: pega X e Y de la marca 1.", style="Chico.TLabel").pack(anchor="w")

    def _ent(self, padre, txt, val):
        ttk.Label(padre, text="  %s:" % txt).pack(side="left", padx=(16, 3))
        e = ttk.Entry(padre, width=7)
        e.insert(0, str(val))
        e.pack(side="left")
        return e

    def _ns(self, **kw):
        d = dict(marks=2, roi=None, iters=4, tol=0.1, max_step=2.0, frames=3,
                 debug=False, no_move=False, emit=COORDS, max_idx=8)
        d.update(kw)
        return type("NS", (), d)()

    def run_marcas(self):
        self._run(hv.cmd_run, self._ns(marks=int(self.v_marcas.get()),
                                       iters=int(self.v_it.get()),
                                       tol=float(self.v_tol.get().replace(",", ".")),
                                       roi=self.v_roi.get().strip() or None))

    def run_solo(self):
        self._run(hv.cmd_run, self._ns(no_move=True))

    def _run(self, fn, ns):
        if self.video:
            self.log("se desconectan las camaras: el calculo abre las suyas")
        self.parar_cams()
        self.lst.delete(0, "end")
        self.coords = []
        self._volvio = bool(self.video)
        self._tarea(fn, ns, al_terminar=self._fin_marcas)

    def _fin_marcas(self, _fu):
        """El `run` de hybrid_vision deja las coordenadas en coords.txt."""
        if os.path.exists(COORDS):
            self.coords = [l.strip() for l in
                           open(COORDS, encoding="utf-8").read().splitlines() if l.strip()]
            self.lst.delete(0, "end")
            for l in self.coords:
                self.lst.insert("end", l)
        else:
            self.lst.insert("end", "sin coordenadas: mira el registro de abajo")
        if self._volvio and not self.video:
            self.conectar()

    def _copia_una(self, _):
        s = self.lst.curselection()
        if s:
            self.pegar(self.lst.get(s[0]))

    def copiar(self):
        if not self.coords:
            self.log("aun no hay coordenadas: ejecuta primero")
            return
        self.pegar("\n".join(c.split("  ")[-1] for c in self.coords))

    def pegar(self, txt):
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.log("copiado: %s" % txt)

    def abrir_coords(self):
        if os.path.exists(COORDS):
            os.startfile(COORDS)
        else:
            self.log("no hay coords.txt todavia")

    # --------------------------------------------------------- hoja "ajustes"
    def _hoja_ajustes(self):
        h = ttk.Frame(self.hojas, padding=8)
        self.hojas.add(h, text="  Ajustes  ")
        f = ttk.Frame(h)
        f.pack(fill="x", anchor="n")
        self.campos = {}
        for r, (etq, clave) in enumerate(
                [("IP de la Ruida", "ip"), ("Indice camara cenital", "top_cam"),
                 ("Indice camara cabezal", "head_cam"),
                 ("Estacionamiento X,Y mm", "park"), ("FOV cabezal mm", "head_fov_mm"),
                 ("Marcas a detectar", "marks"), ("Umbral de negro (0=automatico)", "thr"),
                 ("Area minima px", "min_area"),
                 ("Desplazamiento X,Y mm", "cam_offset_mm")]):
            ttk.Label(f, text=etq, width=26).grid(row=r, column=0, sticky="w", pady=3)
            v = self.cfg.get(clave)
            e = ttk.Entry(f, width=26)
            e.insert(0, ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v))
            e.grid(row=r, column=1, sticky="w", pady=3)
            self.campos[clave] = e
        ttk.Button(f, text="Guardar", command=self.guardar_cfg).grid(row=9, column=1,
                                                                     sticky="w", pady=10)
        ttk.Button(f, text="Listar camaras", command=self.scan_cams).grid(row=9, column=0,
                                                                          sticky="w", pady=10)
        ttk.Label(f, text="Si cambias el indice de una camara hay que desconectar y "
                          "volver a conectar (o reiniciar la app) para que se abra la "
                          "buena. El log de todo va tambien a app.log, junto a la app.",
                  style="Chico.TLabel", wraplength=520).grid(row=10, column=0, columnspan=2,
                                                           sticky="w")

    def guardar_cfg(self):
        cfg = hv.load_cfg()
        for clave, e in self.campos.items():
            t = e.get().strip()
            try:
                if clave in ("park", "cam_offset_mm"):
                    v = [float(x) for x in t.replace(";", ",").split(",") if x]
                elif clave in ("top_cam", "head_cam", "marks", "thr", "min_area"):
                    v = int(float(t))
                else:
                    v = t
            except ValueError:
                self.log("Ajustes: %s no vale (%r), se deja como estaba" % (clave, t))
                continue
            cfg[clave] = v
        hv.save_cfg(cfg)
        self.cfg = cfg
        self.sentido()
        self.log("ajustes guardados en %s" % hv.CFG)

    def scan_cams(self):
        self._tarea(hv.cmd_scan, self._ns(max_idx=8),
                    al_terminar=lambda fu: self.log("mira los scan_idx*.png en %s" % DATOS))

    # ---------------------------------------------------------------- pintura
    def _pintar(self):
        if self.video:
            n, top, head = self.video.frame()
            if n != self._n:
                self._n = n
                self._estado_cams(n, top, head)
                if top is not None and head is not None:
                    self._foto(self.im_top, self._con_top(top))
                    self._foto(self.im_head, self._con_head(head))
                    self._cada_paso()
        self.after(33, self._pintar)

    def _foto(self, lab, bgr):
        h, w = bgr.shape[:2]
        e = min(max(1, lab.winfo_width()) / w, max(1, lab.winfo_height()) / h)
        lab.img = ImageTk.PhotoImage(Image.fromarray(
            cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).resize(
            (max(1, int(w * e)), max(1, int(h * e)))))
        lab.configure(image=lab.img)

    def _con_top(self, gris):
        """La cenital con el cabezal encima y la caja de viaje de la maquina."""
        im = cv2.cvtColor(gris, cv2.COLOR_GRAY2BGR)
        if "H" not in self.cfg:
            hv._texto(im, "SIN HOMOGRAFIA: ve a la hoja Calibrar", (12, 30), (0, 0, 255), 0.9)
            return im
        H = np.array(self.cfg["H"], np.float32)
        cal = self.cal_res
        Hinv = np.linalg.inv(H)
        x0, x1, y0, y1 = ruida.Panel.SAFE
        try:
            caja = [hv._px_de_mm(Hinv, (mx, my), cal, im.shape)
                    for mx, my in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            cv2.polylines(im, [np.array(caja, np.int32)], True, (0, 220, 0), 1)
            if self._ok and self.pos:
                u, v = hv._px_de_mm(H, self.pos, cal, im.shape)
                cv2.circle(im, (int(u), int(v)), 16, (0, 0, 255), 2)
                hv._texto(im, "%.0f, %.0f mm" % self.pos, (int(u) + 20, int(v) + 6),
                          (0, 255, 255))
        except Exception as e:
            hv._texto(im, "H no valida: %s" % e, (12, 30), (0, 0, 255), 0.7)
        return im

    def _con_head(self, gris):
        """El cabezal con la cruz y la ventana de busqueda del centrado."""
        im = cv2.cvtColor(gris, cv2.COLOR_GRAY2BGR)
        h, w = im.shape[:2]
        x0, y0, x1, y1 = hv.center_roi(w, h)
        cv2.rectangle(im, (x0, y0), (x1, y1), (0, 200, 0), 1)
        cv2.line(im, (w // 2 - 22, h // 2), (w // 2 + 22, h // 2), (0, 200, 0), 1)
        cv2.line(im, (w // 2, h // 2 - 22), (w // 2, h // 2 + 22), (0, 200, 0), 1)
        return im

    def _cada_paso(self):
        """Posicion del panel a 3 Hz, por el hilo de trabajo: el socket no se
        toca nunca desde la UI."""
        if self._pid is not None:
            return
        self._pid = self._tarea(self.maq.get().pos, 0.6, al_terminar=self._pos)

    def _pos(self, fu):
        self._pid = None
        p = fu.result()
        if p:
            self.pos, self._ok = p, True
            self.lbl_pos.configure(text="posicion:  X = %8.3f    Y = %8.3f mm" % p)
            self.lbl_pan.configure(text="panel %s: contesta" % self.cfg["ip"],
                                   style="Ok.TLabel")
        else:
            self.pos, self._ok = None, False
            self.lbl_pos.configure(text="posicion: el panel %s no contesta"
                                   % self.cfg["ip"])
            self.lbl_pan.configure(text="panel %s: SIN RESPUESTA" % self.cfg["ip"],
                                   style="Mal.TLabel")

    # -------------------------------------------------------------------- OTA
    def ota(self):
        self.lbl_ota.configure(text="mirando en GitHub...")
        self._tarea(actualizar.comprobar, al_terminar=self._ota_vuelve)

    def _ota_vuelve(self, fu):
        hay, msg, rel = fu.result()
        self.lbl_ota.configure(text=msg)
        if not hay:
            return
        if not messagebox.askyesno("Actualizacion", msg + "\n\nInstalar ahora?"):
            return
        a = actualizar.instalador(rel)
        ruta = os.path.join(os.path.dirname(a["name"]), a["name"])
        self.lbl_ota.configure(text="descargando %.0f MB..."
                               % (a.get("size", 0) / 1048576.0))
        self._tarea(self._baja, a["browser_download_url"], ruta,
                    al_terminar=lambda fu: self.salir())

    def _baja(self, url, ruta):
        for tot in actualizar.descargar(url, ruta):
            if tot is None:
                break
            self.log("descargados %.0f MB" % (tot / 1048576.0))
        actualizar.lanzar(ruta)
        return True

    # ----------------------------------------------------------------- salida
    def abrir_log(self):
        if not os.path.exists(LOGF):
            self.log("todavia no hay app.log")
            return
        os.startfile(LOGF)

    def salir(self):
        self._soltar()
        self.parar_cams()
        try:
            self.pool.submit(self.maq.cerrar).result(timeout=5)
        except Exception:
            pass
        self.pool.shutdown(wait=False)
        self.destroy()


if __name__ == "__main__":
    if sys.stdout is None:            # ejecutable sin consola (--windowed)
        sys.stdout = open(LOGF, "a", encoding="utf-8", buffering=1)
    App().mainloop()
