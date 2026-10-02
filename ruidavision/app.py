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
import tempfile
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, simpledialog, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

import hybrid_vision as hv
import ruida
from ruidavision import VERSION
from ruidavision import actualizar

# Donde estan los datos. Instalada, la app vive en Archivos de programa, que
# es de solo lectura para un usuario normal. Si se ejecuta desde el codigo y ya
# existe el calibrado de una instalacion, se reutiliza para no mover con otra H.
CONGELADO = getattr(sys, "frozen", False)
BASE = os.path.dirname(sys.executable) if CONGELADO else hv.HERE


def _directorio_datos(congelado, base, localappdata):
    usuario = os.path.join(localappdata or base, "Ruida Vision")
    if congelado or (localappdata and
                     os.path.isfile(os.path.join(usuario, "calib.json"))):
        return usuario
    return base


DATOS = _directorio_datos(CONGELADO, BASE, os.environ.get("LOCALAPPDATA"))


def _marcas_calibracion_en_area(found, H):
    """Devuelve todos los candidatos y separa los que la homografia actual
    proyecta fuera de la caja segura de la maquina."""
    if H is None:
        return [(u, v) for u, v, _ in found], []
    dentro, fuera = hv.marcas_utiles(found, np.array(H, np.float32))
    pixels = [tuple(map(float, px)) for _, px in dentro + fuera]
    pixels_fuera = [tuple(map(float, px)) for _, px in fuera]
    return pixels, pixels_fuera


def _detectar_candidatos_calibracion(gray, cfg, thr, min_area):
    if not 0 <= int(thr) <= 255:
        raise ValueError("el umbral debe estar entre 0 y 255")
    min_area = max(40, int(min_area))
    if min_area > int(cfg["max_area"]):
        raise ValueError("el area minima no puede superar el area maxima")
    found = hv.find_marks(
        gray, min_area=min_area, max_area=cfg["max_area"], thr=int(thr),
        merge=6, min_circularity=0.55, max_aspect_ratio=1.5)
    pixels, pixels_fuera = _marcas_calibracion_en_area(found, cfg.get("H"))
    fuera_mm = []
    if cfg.get("H"):
        _, fuera = hv.marcas_utiles(found, np.array(cfg["H"], np.float32))
        fuera_mm = [(float(mm[0]), float(mm[1])) for mm, _ in fuera]
    return pixels, pixels_fuera, fuera_mm


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
# Teclas que cambian el paso del toque, como el desplegable pero sin soltar el
# WASD. `minus` es el guion de la fila de numeros y `KP_Subtract` el del teclado
# numerico, que es donde algunos teclados lo ponen.
PASOS = {"minus": -1, "KP_Subtract": -1, "plus": 1, "KP_Add": 1, "equal": 1}
SIN_CAM = "camaras: sin abrir (pulsa Conectar)"
LASER = "LASER APAGADO: esto mueve el cabezal, no dispara"


def _dpi_awareness():
    """Que Windows no deforme la ventana en pantallas con escalado.

    Sin esto el proceso es "DPI unaware": Windows lo dibuja a 96 DPI y lo
    estira como una foto. En un portatil al 125-150 % sale borrosa, y el texto
    y los botones se salen de la ventana, que es el mismo sintoma que se
    acababa de arreglar con "Quitar punto". Per-Monitor V2 es lo que entiende
    Windows 10 1703 en adelante; si no esta, al menos el nivel del sistema.

    `SetProcessDpiAwarenessContext` toma un HANDLE, o sea un puntero de 64 bits:
    hay que declararlo, porque si no `ctypes` pasa un entero de 32 y la llamada
    falla sin decir nada.
    """
    if sys.platform != "win32":
        return "escalado: no hace falta fuera de Windows"
    import ctypes
    try:
        v2 = ctypes.windll.user32.SetProcessDpiAwarenessContext
        v2.argtypes = [ctypes.c_void_p]
        v2.restype = ctypes.c_int
        if v2(ctypes.c_void_p(-4)):        # -4 = PER_MONITOR_AWARE_V2
            return "escalado: por monitor (V2)"
    except (AttributeError, OSError):
        pass                            # Windows anterior a 1703
    try:                                # Windows 8.1
        if ctypes.windll.shcore.SetProcessDpiAwareness(2):
            return "escalado: por monitor"
    except (AttributeError, OSError):
        pass
    try:                                # Windows Vista y posteriores
        if ctypes.windll.user32.SetProcessDPIAware():
            return "escalado: del sistema"
    except (AttributeError, OSError):
        pass
    # PyInstaller puede haberlo puesto ya en el arranque, y entonces esto no
    # tiene arreglo: se deja como esta y el registro lo dice.
    return "escalado: el de por defecto de Windows (puede salir borrosa)"


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


def boton(padre, texto, accion=None, **kw):
    """Boton con su nombre encima. Los iconos se probaron y estorbaban: con la
    ventana estrecha habia que adivinar, y el nombre cabe en la barra sin
    problema. Sin adornos ni tooltips: lo que dice el boton es lo que hace."""
    return ttk.Button(padre, text=texto, command=accion, **kw)


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


def _posicion_valida(p):
    if p is None:
        return False
    x0, x1, y0, y1 = ruida.Panel.SAFE
    return x0 - 1 <= p[0] <= x1 + 1 and y0 - 1 <= p[1] <= y1 + 1


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
        self.detectadas_fuera = []
        self.esc = 1.0
        self.ox = self.oy = 0
        self.id = None
        self.bind("<Configure>", lambda e: self._dibujar())
        self.bind("<Button-1>", self._clic)

    def poner(self, bgr, detectadas=(), detectadas_fuera=()):
        self.foto, self.cruz, self.id = bgr, None, None
        self.detectadas = list(detectadas)
        self.detectadas_fuera = list(detectadas_fuera)
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
        fuera = set(self.detectadas_fuera)
        todas = self.detectadas + [
            px for px in self.detectadas_fuera if px not in self.detectadas]
        for i, (u, v) in enumerate(todas, 1):
            x, y = u * self.esc + self.ox, v * self.esc + self.oy
            r = max(12.0, 8 * self.esc)
            color = "#ffbf00" if (u, v) in fuera else "#00ff88"
            tags = ("marcas", "marca-%d" % i)
            self.create_oval(x - r, y - r, x + r, y + r,
                             outline="#ffffff", width=5, tags=tags)
            self.create_oval(x - r, y - r, x + r, y + r,
                             outline=color, width=3, tags=tags)
            self.create_line(x - r - 5, y, x + r + 5, y,
                             fill=color, width=2, tags=tags)
            self.create_line(x, y - r - 5, x, y + r + 5,
                             fill=color, width=2, tags=tags)
            self.create_text(x + r + 4, y - r - 2, text=str(i),
                             fill="#ffffff", anchor="sw",
                             font=("Segoe UI", 12, "bold"), tags=tags)

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


class Deslizable(ttk.Frame):
    """Una hoja que cabe en la ventana y se desplaza si no cabe entera.

    Marcas lleva los cinco pasos escritos, el visor, la lista de coordenadas y
    los otros dos parrafos: mas de 900 px de alto, y la ventana minima son 620.
    Sin esto lo de abajo se ve a medias y no hay forma de llegar al final.

    Solo para hojas de contenido FIJO. Vivo y Calibrar no llevan esto a proposito:
    ahi lo que crece es el visor de las camaras, que necesita el `expand` del
    `pack`, y dentro de un `Canvas` de scroll eso no existe (el hijo se queda en
    su alto pedido)."""

    def __init__(self, padre):
        super().__init__(padre)
        self.lienzo = tk.Canvas(self, highlightthickness=0, bd=0,
                                background=ttk.Style().lookup("TFrame", "background")
                                or self.cget("background"))
        self.barra = ttk.Scrollbar(self, orient="vertical",
                                   command=self.lienzo.yview)
        self.lienzo.configure(yscrollcommand=self.barra.set)
        self.lienzo.pack(side="left", fill="both", expand=True)
        self.barra.pack(side="right", fill="y")
        self.interior = ttk.Frame(self.lienzo, padding=8)
        self._id = self.lienzo.create_window((0, 0), window=self.interior,
                                             anchor="nw")
        self.interior.bind("<Configure>", self._medida)
        self.lienzo.bind("<Configure>", self._ancho)
        # Windows manda la rueda a la ventana del FOCO, no al widget que hay
        # debajo del raton: con el raton sobre el texto, sin esto no se
        # desplaza. `bind_all` porque Tk no propaga la rueda a los hijos.
        self.lienzo.bind_all("<MouseWheel>", self._rueda)

    def _medida(self, e):
        self.lienzo.configure(scrollregion=self.lienzo.bbox("all"))

    def _ancho(self, e):
        # El interior toma el ancho del lienzo: si no, se queda en el ancho que
        # pidio al construir y el texto se parte por donde le da la gana.
        self.lienzo.itemconfigure(self._id, width=e.width)

    def _rueda(self, e):
        w = e.widget
        # Los widgets con scroll propio se desplazan ellos; el resto de la hoja,
        # si.
        if isinstance(w, (tk.Listbox, tk.Text, tk.Canvas, ttk.Treeview,
                          ttk.Entry, tk.Entry, ttk.Combobox)):
            return
        if not (w is self or w is self.lienzo or str(w).startswith(str(self) + ".")):
            return
        self.lienzo.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"


# --------------------------------------------------------------------- la app

class App(tk.Tk):
    def __init__(self):
        # Antes de `super()`: la ventana es lo que hay que crear ya al tanto del
        # escalado. Ademas se queda en el registro, que es como se sabe que ha
        # surtido efecto en el PC de verdad.
        self.dpi = _dpi_awareness()
        super().__init__()
        self.title("Ruida Vision %s" % VERSION)
        self.geometry("1200x780")
        self.minsize(940, 620)
        self.cfg = hv.load_cfg()
        self.lineas = queue.Queue()      # texto del registro
        self.trabajos = queue.Queue()    # cosas que se ejecutan en la UI
        self._log_candado = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maquina")
        self.maq = Maquina()
        self.video = None
        self.puntos = []                 # [(px, py, X mm, Y mm)]
        self.numeros = []                # numero que dice cada punto (1, 2, 3...)
        self.fovpts = []
        self.modo = None                 # None | "fov"
        self.coords = []
        self._volvio = False
        self._n = 0
        self._pid = None
        self._ok = None
        self.pos = None
        self._jog_d = None               # direccion de jog en curso
        self._jog_t = None               # la pulsacion se esta volviendo continua
        self._jog_p = None               # parada pendiente (debounce del repeat)
        self._jog_ev = None              # Event del continuo en curso
        self._native_move_active = False
        self._native_move_fault = False
        self._cal_frame = None
        self.cal_thr = int(self.cfg.get("thr", 0))
        self.cal_min_area = max(40, int(self.cfg.get("min_area", 40)))
        self._jog_step_labels = []
        # Que visores en vivo hay que pintar en cada hoja. Se dibujan solo los de
        # la hoja visible: pintar los de las dos hojas cada 33 ms son seis
        # conversiones de imagen por segundo y frame que no se ven.
        self.vivos = {}

        self._estilo()
        self._cabecera()
        self.hojas = ttk.Notebook(self)
        self.hojas.pack(fill="both", expand=True, padx=8, pady=(4, 0))
        self._hoja_vivo()
        self._hoja_calibrar()
        self._hoja_marcas()
        self._hoja_ajustes()
        self._pie()
        self._wrap_al_ancho()
        self.protocol("WM_DELETE_WINDOW", self.salir)
        self.hojas.bind("<<NotebookTabChanged>>", self._cambio_hoja)
        self.bind("<KeyPress>", self._tecla)
        self.bind("<KeyRelease>", self._suelta_tecla)
        self.after(33, self._pintar)
        self.after(120, self._desdovar)
        # Las camaras solas al arrancar: hadia que pulsar "Conectar camaras".
        # Con 200 ms la ventana ya esta mappeada, que es lo que necesita cv2
        # para abrir la camara.
        self.after(200, self.conectar)
        # Y la actualizacion tambien, sin boton: el de pie no se veia y nadie lo
        # pulsaba, con lo que las versiones nuevas solo llegaban a quien se
        # acordaba. Esto SOLO mira que hay; preguntar si se instala sigue siendo
        # de `_ota_vuelve`, y por el registro se ve que se ha mirado.
        self.after(800, self.ota)
        self.log("App %s. Datos en %s. %s" % (VERSION, DATOS, LASER))
        self.log(self.dpi)

    # -- aspecto
    def _wrap_al_ancho(self):
        """Que todo texto con `wraplength` siga al ancho de su marco.

        Los `wraplength` fijos (980 px en Marcas) salen del limite: al bajar la
        ventana a 940 px, que es el `minsize`, el texto se va por la derecha y
        no se ve el final de la frase. Aqui cada etiqueta que lo tenga pasa a
        medir lo que mide su marco, y asi el texto se reparte en cuanto se
        estrecha la ventana.
        """
        for lbl in self._etiquetas(self):
            fijo = int(lbl.cget("wraplength") or 0)
            if fijo <= 0:
                continue            # los que no envuelven, se dejan como estan
            marco = lbl.master

            def ajusta(e, lbl=lbl, marco=marco, fijo=fijo):
                # Solo encoge: en ventana ancha manda el valor que ya estaba
                # puesto, que es como se maquetó; al estrechar, el del marco.
                ancho = max(120, min(fijo, e.width - 12))
                if int(lbl.cget("wraplength")) != ancho:
                    lbl.configure(wraplength=ancho)

            marco.bind("<Configure>", ajusta, add="+")

    @staticmethod
    def _etiquetas(padre):
        for w in padre.winfo_children():
            if isinstance(w, ttk.Label):
                yield w
            yield from App._etiquetas(w)

    def _estilo(self):
        s = ttk.Style(self)
        for t in ("vista", "winnative", "clam"):
            if t in s.theme_names():
                s.theme_use(t)
                break
        s.configure(".", font=("Segoe UI", 10))
        s.configure("TButton", padding=(10, 6))
        s.configure("Jog.TButton", padding=(1, 0), font=("Segoe UI", 8))
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
        # El log va con barra: siete lineas no se leen con la rueda y no hay
        # forma de volver atras. Y `wrap="word"` en vez de `wrap="none"`: con
        # "none" la linea se sigue por debajo del borde y lo que no cabe no se
        # ve nunca, que es justo lo que uno busca en el registro.
        marco = ttk.Frame(self)
        marco.pack(fill="both", side="bottom", padx=8, pady=(4, 0))
        self.txt = tk.Text(marco, height=7, bg="#101418", fg="#cfd8dc",
                           font=("Consolas", 9), wrap="word", state="disabled")
        barra = ttk.Scrollbar(marco, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=barra.set)
        self.txt.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        # Grid y no pack: el boton va con sticky para que no se estire
        # horizontalmente y el aviso del OTA (que es largo) no lo aplaste. El
        # boton de buscar actualizaciones se fue: la app mira solo al arrancar.
        f = ttk.Frame(self, padding=(10, 6))
        f.pack(fill="x", side="bottom")
        f.columnconfigure(0, weight=1)
        self.lbl_ota = ttk.Label(f, text="", style="Chico.TLabel", wraplength=560,
                                 justify="left")
        self.lbl_ota.grid(row=0, column=0, sticky="w")
        boton(f, "Ver registro", self.abrir_log).grid(
            row=0, column=1, sticky="e", padx=(6, 0))

    # -------------------------------------------------------------- fontaneria
    def log(self, txt):
        """Guarda el registro en disco y encola su copia para la interfaz."""
        linea = str(txt)
        with self._log_candado:
            os.makedirs(DATOS, exist_ok=True)
            with open(LOGF, "a", encoding="utf-8") as f:
                f.write(linea + "\n")
                f.flush()
        self.lineas.put(linea)

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
            with contextlib.redirect_stdout(_Tee(self.log)):
                return fn(*a, **kw)
        f = self.pool.submit(trabajo)
        if al_terminar:
            def terminado(fu):
                if fu.exception() is not None:
                    self.log("ERROR: %s" % fu.exception())
                self.call(al_terminar, fu)
            f.add_done_callback(terminado)
        else:
            f.add_done_callback(
                lambda fu: self.log("ERROR: %s" % fu.exception())
                if fu.exception() is not None else None)
        return f

    # ------------------------------------------------------------ hoja "vivo"
    def _hoja_vivo(self):
        h = ttk.Frame(self.hojas, padding=8)
        self.hojas.add(h, text="  Vivo  ")
        self.i_vivo = self.hojas.index(h)
        b = ttk.Frame(h)
        b.pack(fill="x")
        # Los botones van en grid y en dos filas, no en un pack de una: con los
        # nombres encima todo en una fila pedia 1500 px y en una ventana de 1200
        # el ultimo boton ("Buscar camaras") quedaba fuera, sin verse. Wrap por
        # filas, que es lo que hace la gente en papel.
        # "Estacionar" no esta: iba al estacionamiento de calib.json (20,20) y
        # "Origen 0,0" va a la esquina, que a 20 mm de diferencia es el mismo
        # sitio. Dos botones para el mismo movimiento era una duda por trabajo.
        for col, (nombre, accion) in enumerate((
                ("Conectar camaras", self.conectar),
                ("Desconectar", self.parar_cams),
                ("Parar", self.stop),
                ("Origen 0,0", lambda: self.ir_a(0.0, 0.0)),
                ("Buscar camaras", self.scan_cams),
        )):
            btn = boton(b, nombre, accion)
            if nombre == "Origen 0,0" and not ruida.NATIVE_POSITION_MOVE_ENABLED:
                btn.configure(state="disabled")
            if nombre == "Origen 0,0":
                self.btn_origen = btn
            btn.grid(row=0, column=col, padx=2, sticky="w")
        # El paso, como en LightBurn: un numero que se sube y se baja con -/+
        # en saltos de 0.1, sin desplegable. El desplegable obligaba a soltar el
        # WASD justo cuando hace falta el paso fino, que es con el cabezal en
        # la mano.
        ttk.Label(b, text="paso (toque):", style="Chico.TLabel").grid(
            row=1, column=0, padx=(0, 2), sticky="w", pady=(4, 0))
        boton(b, "−", lambda: self._cambia_paso(-1)).grid(
            row=1, column=1, sticky="w", pady=(4, 0))
        self.lbl_paso = ttk.Label(b, text="", width=9, anchor="center",
                                  font=("Consolas", 11))
        self.lbl_paso.grid(row=1, column=2, padx=2, sticky="w", pady=(4, 0))
        boton(b, "+", lambda: self._cambia_paso(1)).grid(
            row=1, column=3, sticky="w", pady=(4, 0))
        self._pon_paso(0.5)
        ttk.Label(b, text="mantener = continuo", style="Chico.TLabel").grid(
            row=1, column=4, padx=(10, 0), sticky="w", pady=(4, 0))
        self.lbl_dir = ttk.Label(b, text="", style="Chico.TLabel")
        self.lbl_dir.grid(row=1, column=5, padx=18, sticky="w", pady=(4, 0))

        self.lbl_pos = ttk.Label(h, text="posicion: -", font=("Consolas", 11))
        self.lbl_pos.pack(anchor="w", pady=(6, 0))

        cuerpo = ttk.Frame(h)
        cuerpo.pack(fill="both", expand=True, pady=6)
        self.im_top = self._marco(cuerpo, "CENITAL")
        self.im_head = self._marco(cuerpo, "CABEZAL (microscopio)")
        self.vivos[self.i_vivo] = (self.im_top, self.im_head)

        m = ttk.Frame(h)
        m.pack(fill="x")
        ttk.Label(m, text="Mover:", style="Chico.TLabel").pack(side="left", padx=(0, 8))
        for d in ("+Y", "+X", "-Y", "-X"):
            b2 = boton(m, d)
            b2.bind("<ButtonPress-1>", lambda e, d=d: self._toque(d))
            b2.bind("<ButtonRelease-1>", lambda e: self._suelta())
            b2.pack(side="left", padx=2)

    def _marco(self, padre, titulo, alto=10, celda=None):
        """Visor con su marco. En `celda` (fila, columna) se pone en la rejilla
        de Calibrar; sin ella, en un `pack` a la izquierda, que es lo que hace
        la hoja Vivo."""
        f = ttk.LabelFrame(padre, text=titulo, padding=4)
        if celda:
            f.grid(row=celda[0], column=celda[1], padx=4, pady=4, sticky="nsew")
        else:
            f.pack(side="left", fill="both", expand=True, padx=4)
        lab = tk.Label(f, bg="#0d0d0d", text="sin imagen", anchor="center",
                       font=("Consolas", 9), height=alto, width=42)
        lab.pack(fill="both", expand=True)
        return lab

    def _controles_jog(self, padre):
        """Pad compacto compartido por las hojas Calibrar y Print and Cut."""
        mov = ttk.LabelFrame(padre, text="Jog manual", padding=3)
        for d, r, c in (("+Y", 0, 1), ("-X", 1, 0),
                        ("+X", 1, 2), ("-Y", 2, 1)):
            btn = ttk.Button(mov, text=d, style="Jog.TButton", width=4)
            btn.bind("<ButtonPress-1>", lambda e, d=d: self._toque(d))
            btn.bind("<ButtonRelease-1>", lambda e: self._suelta())
            btn.grid(row=r, column=c, padx=1, pady=1, ipadx=1, ipady=0)
        pasos = ttk.Frame(mov)
        pasos.grid(row=1, column=1, padx=1)
        cal_menos = ttk.Button(pasos, text="−", style="Jog.TButton", width=2,
                               command=lambda: self._cambia_paso(-1))
        cal_menos.pack(side="left")
        etiqueta = ttk.Label(pasos, text="%.1f" % self.paso_mm,
                             width=4, anchor="center", style="Chico.TLabel")
        etiqueta.pack(side="left")
        cal_mas = ttk.Button(pasos, text="+", style="Jog.TButton", width=2,
                             command=lambda: self._cambia_paso(1))
        cal_mas.pack(side="left")
        for widget in (pasos, etiqueta, cal_menos, cal_mas):
            widget.bind("<MouseWheel>", self._rueda_paso)
            widget.bind("<Button-4>", self._rueda_paso)
            widget.bind("<Button-5>", self._rueda_paso)
        self._jog_step_labels.append(etiqueta)
        return mov

    # -- camaras
    def conectar(self):
        if self.video and self.video.is_alive():
            return
        self.video = None
        self.lbl_cam.configure(text="camaras: abriendo...", style="Chico.TLabel")
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

    # -- jogging: toque corto = un pulso deadbeat (el paso de la lista, que es
    # el que centra a 0.1 mm); mantener = UN keydown continuo hasta soltar. El
    # continuo es lo que hace util la maquina: a pulsos de 3.4 mm hay que
    # pulsar 120 veces para cruzar la cama, y a 5 mm/s de jog se tarda lo mismo
    # que yendo a pie. La tecla se suelta siempre: la del continuo la suelta
    # jog_hold, y la del toque tambien sale por el finally.
    LARGO_MS = 220          # menos que esto es un toque, no un desplazamiento

    def _toque(self, d):
        """Boton o tecla pulsados."""
        if self._native_move_active or self._native_move_fault:
            self.log("jog bloqueado: hay un viaje nativo activo o sin confirmar")
            return
        if self._jog_p:                 # se estaba soltando: reengancha
            self.after_cancel(self._jog_p)
            self._jog_p = None
        if self._jog_ev is not None or self._jog_t is not None:
            return                      # autorrepeticion: ya se esta moviendo
        if not self.video:
            self.log(SIN_CAM)
            return
        self._jog_d = d
        self._jog_t = self.after(self.LARGO_MS, self._sigue)

    def _sigue(self):
        """Llevaba LARGO_MS pulsado: pasa a movimiento continuo."""
        self._jog_t = None
        self._jog_ev = threading.Event()
        self._tarea(self._mueve, self._jog_d, self._jog_ev)

    def _mueve(self, d, ev):
        """En el hilo de trabajo: keydown continuo y keyup al soltar."""
        pan = self.maq.get().pan
        if pan is None:
            self.log("sin panel: no se puede mover")
            return None
        pan.resume()
        return pan.jog_hold(d, ev)

    def _paso(self, d):
        """En el hilo de trabajo: un pulso deadbeat del paso elegido."""
        pan = self.maq.get().pan
        if pan is None:
            self.log("sin panel: no se puede mover")
            return None
        pan.resume()
        return pan.hold(d, hv.ms_de_paso(self.paso_mm))

    def _suelta(self, d=None):
        """Se solto el boton o la tecla. El corte lleva 60 ms de retardo porque
        en X11 la autorrepeticion manda keyup+keydown, y cortar de golpe
        pararia y reanudaria el jog en cada repeticion."""
        if d is not None and self._jog_d != d:
            return                      # soltar Enter no para el jog de W
        if not self._jog_p:
            self._jog_p = self.after(60, self._fin)

    def _jog_en_curso(self):
        return (self._jog_t is not None or self._jog_p is not None
                or self._jog_ev is not None)

    def _fin(self):
        """Ya no hay tecla pulsada: corta el continuo, o da el paso fino."""
        self._jog_p = None
        if self._jog_t is not None:
            self.after_cancel(self._jog_t)
            self._jog_t = None
            self._tarea(self._paso, self._jog_d)
        if self._jog_ev is not None:
            self._jog_ev.set()
            self._jog_ev = None

    def _dir(self, e):
        """La direccion que pide una tecla, o None."""
        d = FLECHAS.get(e.keysym)
        if not d:
            d = hv._mapa_wasd(self.cfg, self.cal_res).get(e.keysym.lower())
        return d

    def _tecla(self, e):
        """Las flechas van por su tabla (el teclado, no como este montada la
        camara) y las letras por la homografia, igual que en la linea de
        ordenes. Mantener la tecla mueve de forma continua."""
        if self._escribiendo():
            return                      # se esta escribiendo: no muevas nada
        if e.keysym in PASOS:
            self._cambia_paso(PASOS[e.keysym])
            return "break"
        d = self._dir(e)
        if d:
            self._toque(d)
            return "break"

    def _cambia_paso(self, d):
        """`-` y `+` cambian el paso del toque, sin soltar el WASD.

        El paso se elegia solo con el desplegable y con el cabezal en la mano
        habia que dejar el raton: el paso fino se usa justo cuando se esta
        moviendo, y el desplegable no se puede tocar sin soltar la tecla."""
        self._pon_paso(self.paso_mm + d * 0.1)

    def _pon_paso(self, mm):
        """Fija el paso del toque. Se guarda redondeado a 0.1 mm porque es lo que
        se ve en la etiqueta, y +- van en saltos de 0.1."""
        self.paso_mm = round(max(hv.PASO_MIN, min(hv.PASO_MAX, mm)), 1)
        self.lbl_paso.configure(text="%.1f mm" % self.paso_mm)
        for label in self._jog_step_labels:
            label.configure(text="%.1f" % self.paso_mm)

    def _rueda_paso(self, e):
        delta = getattr(e, "delta", 0)
        if delta:
            cambio = 0.5 if delta > 0 else -0.5
        else:
            cambio = 0.5 if getattr(e, "num", None) == 4 else -0.5
        self._pon_paso(self.paso_mm + cambio)
        return "break"

    def _suelta_tecla(self, e):
        if self._escribiendo():
            return
        d = self._dir(e)
        if d:
            self._suelta(d)
            return "break"

    def _cambio_hoja(self, e=None):
        """Al cambiar de hoja se suelta el toque continuo y el foco vuelve a la
        ventana. Sin esto, un WASD mantenido mientras se pulsa otra pestana deja
        el cabezal andando (el KeyRelease lo recibe la hoja nueva), y el foco se
        queda en un Entry de la hoja que ya no se ve, donde teclear no mueve
        nada."""
        self._suelta()
        self.focus_set()

    def _escribiendo(self):
        """Con el foco en un campo, teclear es escribir, no mover el cabezal."""
        return isinstance(self.focus_get(), (ttk.Entry, tk.Entry))

    def park(self):
        self._tarea(self.maq.get().park)

    def stop(self):
        if self._native_move_active:
            detalle = ("Parar no interrumpe el viaje nativo a coordenadas. "
                       "Usa el paro fisico de la controladora ante una emergencia.")
            self.log(detalle)
            messagebox.showwarning("Viaje nativo en curso", detalle, parent=self)
            return
        for attr in ("_jog_t", "_jog_p"):
            after_id = getattr(self, attr)
            if after_id is not None:
                self.after_cancel(after_id)
                setattr(self, attr, None)
        if self._jog_ev is not None:
            self._jog_ev.set()
            self._jog_ev = None
        self._jog_d = None
        try:
            pan = self.maq.get().pan
            if pan is None:
                self.log("PARAR: sin panel conectado")
                return
            pan.stop()
            self.log("PARAR: movimiento detenido; teclas del panel liberadas")
        except Exception as e:
            self.log("ERROR al parar el movimiento: %s" % e)
            messagebox.showerror("Parar movimiento",
                                 "No se pudo enviar PARAR:\n\n%s" % e,
                                 parent=self)

    # ------------------------------------------------------ hoja "calibrar"
    def _hoja_calibrar(self):
        self.prompt = ""
        h = ttk.Frame(self.hojas, padding=8)
        self.hojas.add(h, text="  Calibrar  ")
        self.i_cal = self.hojas.index(h)
        b = ttk.Frame(h)
        b.pack(fill="x")
        for col in range(4):
            b.columnconfigure(col, weight=1, uniform="cal_b")
        for col, (nombre, cmd) in enumerate((
                ("1. Estacionar", self.cal_park),
                ("Congelar foto", self.cal_foto),
                ("2. Ajustar H", self.cal_ajusta),
                ("Quitar punto", self.cal_quita))):
            btn = boton(b, nombre, cmd)
            if nombre == "1. Estacionar" and not ruida.Panel.AUTOMATIC_MOVE_ENABLED:
                btn.configure(state="disabled")
            if nombre == "1. Estacionar":
                self.btn_cal_park = btn
            btn.grid(row=0, column=col, padx=2, sticky="w")
        for col, (nombre, cmd) in enumerate((
                ("Borrar puntos", self.cal_limpia),
                ("3. Medir FOV", self.fov_foto),
                ("4. Offset laser", self.off_pregun))):
            boton(b, nombre, cmd).grid(row=1, column=col, padx=2, sticky="w",
                                       pady=(3, 0))
        self.lbl_fov = ttk.Label(b, text="", style="Chico.TLabel")
        self.lbl_fov.grid(row=1, column=3, padx=6, sticky="w", pady=(3, 0))
        self._fov_muestra()
        ttk.Label(b, text="  el clic en la foto guarda el punto solo; en la tabla, "
                           "Supr quita el marcado", style="Chico.TLabel").grid(
            row=2, column=0, padx=10, sticky="w", pady=(3, 0))

        controles = ttk.Frame(h)
        controles.pack(fill="x", pady=(2, 0))
        self._controles_jog(controles).pack(side="left", padx=(0, 6))
        boton(controles, "Conectar cámaras", self.conectar).pack(
            side="left", padx=(0, 8))
        det = ttk.LabelFrame(controles, text="Detector foto cenital", padding=3)
        det.pack(side="left", fill="x", expand=True)
        self.cal_thr_var = tk.StringVar(value=str(self.cal_thr))
        self.cal_area_var = tk.StringVar(value=str(self.cal_min_area))
        ttk.Label(det, text="Umbral:").pack(side="left")
        ttk.Spinbox(det, from_=0, to=255, increment=5, width=5,
                    textvariable=self.cal_thr_var).pack(side="left", padx=2)
        ttk.Label(det, text="Area min.:").pack(side="left", padx=(5, 0))
        ttk.Spinbox(det, from_=40, to=self.cfg["max_area"], increment=10,
                    width=6, textvariable=self.cal_area_var).pack(
                        side="left", padx=2)
        self.btn_cal_apply = boton(det, "Aplicar en foto", self.cal_detector_aplicar)
        self.btn_cal_apply.configure(state="disabled")
        self.btn_cal_apply.pack(side="left", padx=3)
        ttk.Label(det, text="0 = Otsu; no cambia Print and Cut.",
                  style="Chico.TLabel").pack(side="left", padx=4)

        cuerpo = ttk.Frame(h)
        cuerpo.pack(fill="both", expand=True, pady=6)
        # Los cuatro elementos (las dos camaras en vivo, la foto congelada y la
        # tabla de puntos) en una rejilla de 2x2 con filas y columnas del mismo
        # peso: los cuatro miden EXACTAMENTE lo mismo. Antes era un PanedWindow
        # con la izquierda al 75% y la foto encima de las camaras, y el reparto
        # era el que saliera: la foto se comia la hoja y la tabla una franja.
        for r in (0, 1):
            cuerpo.rowconfigure(r, weight=1, uniform="cal")
            cuerpo.columnconfigure(r, weight=1, uniform="cal")
        cal_top = self._marco(cuerpo, "CENITAL (en vivo)", alto=7, celda=(0, 0))
        cal_head = self._marco(cuerpo, "CABEZAL (en vivo)", alto=7, celda=(0, 1))
        self.vivos[self.i_cal] = (cal_top, cal_head)
        self.foto = Foto(cuerpo, self._clic)
        self.foto.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        d = ttk.Frame(cuerpo, padding=6)
        d.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)
        # Las cuatro celdas, para poder medirlas: es lo que se ve, no los
        # widgets de dentro, que tienen-request distinto (la tabla pide 16 filas).
        self.celdas_cal = (cal_top.master, cal_head.master, self.foto, d)
        ttk.Label(d, text="Puntos:  pixel de la cenital = maquina",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        nb = ttk.Frame(d)
        nb.pack(fill="x", pady=(2, 2))
        boton(nb, "Marcar con numero", self.cal_numero).pack(side="left")
        boton(nb, "Renumerar", self.cal_reordena).pack(side="left", padx=3)
        self.tabla = ttk.Treeview(d, columns=("n", "px", "py", "x", "y"), show="headings",
                                  height=16)
        for c, t, w in (("n", "n", 32), ("px", "px", 58), ("py", "py", 58),
                        ("x", "X mm", 78), ("y", "Y mm", 78)):
            self.tabla.heading(c, text=t)
            self.tabla.column(c, width=w, anchor="center")
        self.tabla.pack(fill="both", expand=True)
        self.tabla.bind("<Delete>", lambda e: self.cal_quita())
        self.lbl_cal = ttk.Label(d, text="", style="Chico.TLabel", wraplength=320,
                                 justify="left")
        self.lbl_cal.pack(anchor="w", pady=6)

    def cal_park(self):
        if not ruida.Panel.AUTOMATIC_MOVE_ENABLED:
            self.log("Estacionar ignorado: movimiento automatico suspendido")
            return
        self._tarea(self.maq.get().park)
        self.log("estacionamiento en %s" % (self.cfg["park"],))

    def _congelar(self, which, n=3):
        """Foto parada de una camara. El hilo de video se para antes (en la UI,
        no aqui dentro: tocar un widget desde el hilo de trabajo parte la app):
        dos VideoCapture sobre el mismo indice se reparten los fotogramas."""
        if which == "top":
            try:
                thr, min_area = self._cal_parametros()
            except ValueError as e:
                self.lbl_cal.configure(text=str(e), style="Mal.TLabel")
                return
        else:
            thr, min_area = 0, 40
        self._volvio = bool(self.video)
        self.parar_cams()
        self._foto_filtrada_area = which == "top" and bool(self.cfg.get("H"))
        cfg = dict(self.cfg)

        def f():
            cap = hv.open_cam(cfg, which)
            try:
                g = hv.grab(cap, n)
                # Marcas SOLO en la cenital: lo que ve el detector encima de la
                # foto es lo unico que dice que una mancha es una marca. En la
                # camara del cabezal no significaria nada.
                if which == "top":
                    pixels, pixels_fuera, fuera_mm = (
                        _detectar_candidatos_calibracion(
                            g, cfg, thr, min_area))
                    if pixels_fuera:
                        self.log("Calibrar: %d candidatos redondos fuera del area "
                                 "segura estimada; se muestran en ambar: %s" %
                                 (len(pixels_fuera), [
                                     (round(mm[0], 1), round(mm[1], 1))
                                     for mm in fuera_mm]))
                    return g, pixels, pixels_fuera, fuera_mm
                return g, [], [], []
            finally:
                cap.release()
        return self._tarea(f, al_terminar=self._foto_lista)

    def _cal_parametros(self):
        try:
            thr = int(self.cal_thr_var.get())
            min_area = int(self.cal_area_var.get())
        except (ValueError, tk.TclError):
            raise ValueError("umbral y area minima deben ser numeros enteros")
        if not 0 <= thr <= 255:
            raise ValueError("el umbral debe estar entre 0 y 255")
        if not 40 <= min_area <= int(self.cfg["max_area"]):
            raise ValueError("el area minima debe estar entre 40 y %d px"
                             % int(self.cfg["max_area"]))
        return thr, min_area

    def cal_detector_aplicar(self):
        if self._cal_frame is None:
            self.lbl_cal.configure(
                text="Primero congela una foto cenital.", style="Mal.TLabel")
            return
        try:
            thr, min_area = self._cal_parametros()
        except ValueError as e:
            self.lbl_cal.configure(text=str(e), style="Mal.TLabel")
            return
        self.btn_cal_apply.configure(state="disabled")
        self._tarea(self._procesa_frame_cal, self._cal_frame.copy(), thr, min_area,
                    al_terminar=self._cal_aplicada)

    def _procesa_frame_cal(self, frame, thr, min_area):
        pixels, fuera, fuera_mm = _detectar_candidatos_calibracion(
            frame, dict(self.cfg), thr, min_area)
        return pixels, fuera, fuera_mm

    def _cal_aplicada(self, fu):
        self.btn_cal_apply.configure(state="normal")
        try:
            pixels, fuera, fuera_mm = fu.result()
        except Exception as e:
            self.lbl_cal.configure(text="No se pudo aplicar la deteccion: %s" % e,
                                   style="Mal.TLabel")
            self.log("ERROR al aplicar detector de calibracion: %s" % e)
            return
        self.foto.poner(self._cal_frame, pixels, fuera)
        self._cal_resultado(pixels, fuera)
        if fuera:
            self.log("Calibrar: candidatos fuera del area segura estimada: %s"
                     % [(round(mm[0], 1), round(mm[1], 1))
                        for mm in fuera_mm])

    def _cal_resultado(self, detectadas, fuera):
        self.lbl_cal.configure(
            text=("%s\n%d candidatos redondos; %d fuera del area segura estimada "
                  "en ambar. El clic se pega al centro cercano."
                  % (self.prompt, len(detectadas), len(fuera))),
            style="Chico.TLabel")

    def cal_foto(self):
        self.modo = None
        self.prompt = "Foto congelada. Pon el cabezal ENCIMA de una marca y pulsa 3."
        self._congelar("top")

    def _fov_muestra(self):
        """Pinta el FOV guardado en las DOS pestanas. Sin refrescar la casilla de
        Ajustes, medir el FOV la dejaba con el valor viejo y el siguiente Guardar
        se lo comia: la medida se perdia por un boton de otra pestana.
        `campos` todavia no existe cuando se pinta la hoja de Calibrar."""
        v = float(self.cfg["head_fov_mm"])
        self.lbl_fov.configure(text="FOV cabezal: %.2f mm" % v)
        e = getattr(self, "campos", {}).get("head_fov_mm")
        if e is not None:
            e.delete(0, "end")
            e.insert(0, "%.2f" % v)

    def fov_foto(self):
        self.fovpts, self.modo = [], "fov"
        self.prompt = ("FOV: haz clic en los DOS extremos de una distancia que sepas "
                       "de verdad; despues te preguntare cuantos mm hay entre ellos.")
        self._congelar("head")

    def _foto_lista(self, fu):
        try:
            g, detectadas, fuera, fuera_mm = fu.result()
            if g is not None:
                self.foto.poner(g, detectadas, fuera)
                if self.modo == "fov":
                    self._cal_frame = None
                    self.btn_cal_apply.configure(state="disabled")
                    texto = "%s\nFoto de la camara del cabezal." % self.prompt
                    self.lbl_cal.configure(text=texto, style="Chico.TLabel")
                else:
                    self._cal_frame = g.copy()
                    self.btn_cal_apply.configure(state="normal")
                    self.lbl_cal.configure(
                        text="%s\n" % self.prompt, style="Chico.TLabel")
                    self._cal_resultado(detectadas, fuera)
                    if fuera:
                        self.log("Calibrar: candidatos fuera del area segura "
                                 "estimada: %s" %
                                 [(round(mm[0], 1), round(mm[1], 1))
                                  for mm in fuera_mm])
        except Exception as e:
            self.lbl_cal.configure(text="No se pudo capturar/procesar la foto: %s"
                                   % e, style="Mal.TLabel")
            self.log("ERROR en foto de calibracion: %s" % e)
        finally:
            reconectar, self._volvio = self._volvio, False
            if reconectar:
                try:
                    self.conectar()
                except Exception as e:
                    self.lbl_cal.configure(
                        text="Foto terminada, pero no se pudieron reconectar las "
                             "camaras: %s" % e, style="Mal.TLabel")
                    self.log("ERROR al reconectar camaras: %s" % e)
            self.sentido()

    def _clic(self, x, y):
        if self.modo == "fov":
            self.fovpts.append((x, y))
            self.lbl_cal.configure(text="%d de 2 clics%s" % (
                len(self.fovpts), ": ahora te pido los mm" if len(self.fovpts) == 2 else ""),
                                   style="Chico.TLabel")
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
        n = 1                              # el libre mas bajo, no len(): tras
        while n in self.numeros:           # borrar un punto, len() repetiria
            n += 1
        self.numeros.append(n)
        self._retabla()
        self.lbl_cal.configure(text="Punto %d:  pixel (%.0f, %.0f) = maquina "
                                   "(%.3f, %.3f). Repite en otro sitio de la cama."
                                   % (len(self.puntos), x, y, p[0], p[1]))

    def cal_ajusta(self):
        if len(self.puntos) < 4:
            self.lbl_cal.configure(text="hacen falta 4 puntos como minimo, y 6 mejor",
                                   style="Mal.TLabel")
            return
        # La homografia no mira los numeros, mira el ORDEN de la lista. Los
        # numeros solo sirven para saber cual es el 1 y cual el 2 cuando hay
        # manchas de mas, asi que avisa si no cuadran en vez de dejar que el
        # error salga 300 fotos mas tarde como una reproyeccion rara.
        if sorted(self.numeros) != list(range(1, len(self.puntos) + 1)):
            self.lbl_cal.configure(
                text="numeros %s: no son 1, 2, 3... Numera a mano o pulsa "
                     "Renumerar" % sorted(self.numeros), style="Mal.TLabel")
            return

        def f():
            # el numero manda: la mancha marcada como 1 se empareja con la
            # primera coordenada de la maquina, la 2 con la segunda... Sin esto
            # el ajuste empareja por orden de lista, que es como salen las
            # manchas, y el 1 y el 2 acababan cambiados de sitio.
            pares = sorted(zip(self.numeros, self.puntos))
            px = [p[:2] for _, p in pares]
            mm = [p[2:] for _, p in pares]
            H, e_max, e_avg, malos = hv.fit_homography(px, mm)
            if H is None:
                return {"ok": False,
                        "message": "No queda modelo: separa mas los puntos y "
                                   "confirma cada posicion/pixel."}
            cfg = hv.load_cfg()
            cfg["H"] = H.tolist()
            # Los que RANSAC rechazo no se guardan: si se guardaran, la
            # siguiente calibracion los volveria a meter.
            cfg["points"] = [[list(px[i]), list(mm[i])]
                             for i in range(len(px)) if i not in malos]
            hv.save_cfg(cfg)
            return {"ok": True, "max_error": e_max, "mean_error": e_avg,
                    "used": len(px) - len(malos),
                    "rejected": [i + 1 for i in malos]}
        self._tarea(f, al_terminar=self._ajustado)

    def _ajustado(self, fu):
        try:
            r = fu.result()
        except Exception as e:
            detalle = "Error al ajustar la homografia: %s" % e
            self.lbl_cal.configure(text=detalle, style="Mal.TLabel")
            self.log(detalle)
            messagebox.showerror("Error de calibracion", detalle, parent=self)
            return
        if isinstance(r, dict) and r.get("ok"):
            self.cfg = hv.load_cfg()
            self.sentido()
            rechazados = r["rejected"]
            detalle = ("Homografia guardada.\n"
                       "Puntos usados: %d de %d.\n"
                       "Error maximo: %.3f mm; medio: %.3f mm."
                       % (r["used"], len(self.puntos), r["max_error"],
                          r["mean_error"]))
            if rechazados:
                detalle += "\nPuntos descartados por inconsistencia: %s." % (
                    ", ".join(map(str, rechazados)))
            self.lbl_cal.configure(text=detalle.replace("\n", " "),
                                   style="Ok.TLabel")
            if r["max_error"] > 1.0 or rechazados:
                detalle += ("\n\nRevisa los puntos descartados y confirma la "
                            "calibracion antes de usarla para movimientos.")
                messagebox.showwarning("Calibracion guardada con advertencias",
                                       detalle, parent=self)
            else:
                messagebox.showinfo("Calibracion confirmada", detalle, parent=self)
        elif isinstance(r, dict):
            detalle = r.get("message", "No se pudo ajustar la homografia.")
            self.lbl_cal.configure(text=detalle, style="Mal.TLabel")
            messagebox.showerror("Calibracion no guardada", detalle, parent=self)
        else:
            detalle = "fallo al ajustar, mira el registro"
            self.lbl_cal.configure(text=detalle, style="Mal.TLabel")
            self.log(detalle)
            messagebox.showerror("Calibracion no guardada", detalle, parent=self)

    def _retabla(self):
        self.tabla.delete(*self.tabla.get_children())
        for i, (px, py, mx, my) in enumerate(self.puntos):
            self.tabla.insert("", "end", values=("%d" % self.numeros[i], "%.0f" % px,
                                                 "%.0f" % py, "%.3f" % mx, "%.3f" % my))

    def cal_numero(self):
        """Pide que numero es el punto MARCADO en la tabla.

        Los reflejos y la luz dejan manchas de mas, y el punto de verdad esta
        amontonado con ellas. Como los seis puntos se leen en el ORDEN de la
        lista, y ese orden es el que sale de la foto, no hay forma de
        arreglarlo borrando: hay que poder decirle a cada fila que numero es.
        Asi el punto 1 puede ser la fila 40.
        """
        sel = self.tabla.selection()
        if not sel:
            self.lbl_cal.configure(text="marca antes una fila de la tabla",
                                   style="Mal.TLabel")
            return
        n = simpledialog.askinteger("Numero de punto", "Este punto es el numero:",
                                    minvalue=1, initialvalue=1, parent=self)
        if n is None:
            return
        # los indices se leen ANTES de _retabla(): al repintar se borran las
        # filas y los ids viejos dejan de existir
        filas = [self.tabla.index(s) for s in sel]
        for i in filas:
            self.numeros[i] = n
        otros = sum(1 for k, v in enumerate(self.numeros)
                    if v == n and k not in set(filas))
        self._retabla()
        self.lbl_cal.configure(
            text="punto %d de la lista = numero %d%s"
                 % (filas[0] + 1, n,
                    "   OJO: ese numero lo tiene tambien otro punto" if otros else ""),
            style="Mal.TLabel" if otros else "Chico.TLabel")

    def cal_reordena(self):
        """Ordena la lista por el numero puesto a mano y renumera 1, 2, 3...

        Asi el orden de la tabla y el emparejamiento con las coordenadas de la
        maquina son lo mismo, sin depender de en que orden salieran las manchas.
        """
        self.puntos = [p for _, p in sorted(zip(self.numeros, self.puntos))]
        self.numeros = list(range(1, len(self.puntos) + 1))
        self._retabla()
        self.lbl_cal.configure(text="ordenado por numero: 1..%d"
                               % len(self.puntos), style="Chico.TLabel")

    def cal_limpia(self):
        self.puntos = []
        self.numeros = []
        self._retabla()
        self.lbl_cal.configure(text="", style="Chico.TLabel")

    def cal_quita(self):
        """Quita el punto SELECCIONADO de la tabla, o el ultimo si no hay nada
        marcado. Con "el ultimo" solamente no habia manera de quitar el punto
        equivocado que caia en medio de la lista, y habia que rehacer los seis.
        Por eso ademas esta la tecla Supr en la tabla."""
        sel = self.tabla.selection()
        if sel:
            # Las filas van en orden de la lista, asi que la fila "n" es el
            # punto n-1. Se borran de mayor a menor para que los indices
            # de los que quedan no se muevan.
            for s in sorted(sel, key=lambda s: self.tabla.index(s), reverse=True):
                del self.puntos[self.tabla.index(s)]
                del self.numeros[self.tabla.index(s)]
        elif self.puntos:
            self.puntos.pop()
            self.numeros.pop()
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
            v = simpledialog.askstring("Offset laser", txt, parent=self)
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
        if (x1, y1) == (x2, y2):
            self.lbl_cal.configure(text="los dos clics han caido en el mismo punto",
                                   style="Mal.TLabel")
            return
        # La distancia se PREGUNTA, no se lee de la casilla de al lado: esa
        # muestra el FOV guardado y releerla hacia que la segunda medida se
        # comiera a si misma y no moviera nada.
        v = simpledialog.askstring(
            "Medir FOV", "Distancia REAL entre los dos clics, en mm:", parent=self)
        if v is None:                       # cancelado: no se toca nada
            return
        try:
            d = float(v.strip().replace(",", "."))
        except ValueError:
            d = 0.0
        h, w = self.foto.foto.shape[:2]
        if d <= 0:
            self.lbl_cal.configure(text="distancia no valida: pon los mm de verdad",
                                   style="Mal.TLabel")
            return
        k = d / ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5
        cfg = hv.load_cfg()
        cfg["head_fov_mm"] = k * w
        hv.save_cfg(cfg)
        self.cfg = cfg
        self._fov_muestra()
        self.lbl_cal.configure(text="head_fov_mm = %.2f  (%.4f mm/px).  Vertical: %.2f mm"
                                % (cfg["head_fov_mm"], k, k * h), style="Ok.TLabel")

    # --------------------------------------------------------- hoja "marcas"
    def _hoja_marcas(self):
        # Deslizable y no un Frame: esta hoja es la unica con texto fijo que no
        # cabe en la ventana minima.
        hoja = Deslizable(self.hojas)
        self.hojas.add(hoja, text="  Marcas (Print and Cut)  ")
        self.i_marcas = self.hojas.index(hoja)
        self.hoja_marcas = hoja
        h = hoja.interior
        x0, y0, x1, y1 = hv.area_trabajo()
        ttk.Label(h, text=(
            "Como va, paso a paso:  1) pon la plantilla en el centro del area de "
            "trabajo y pulsa Detectar los dos puntos: la maquina mira la hoja, descarta las "
            "manchas que caen FUERA del area de %g x %g mm y marca en verde solo las "
            "dos del material, como 1 y 2.  2) Pulsa Mover: el cabezal va al punto 1 "
            "y el boton pasa a Mover 2.  3) Con el cabezal ya ahi, anota el X y la Y "
            "que salen abajo y ponlos en LightBurn > Print and Cut como primer punto. "
            "4) Vuelve y pulsa Mover 2: el cabezal va al punto 2, y a partir de ahi "
            "pon DESACTIVADO el offset de la camara en LightBurn, que aqui ya esta "
            "sumado.  5) Anota el X y la Y del punto 2 y ponlos como segundo punto. "
            "Las dos camaras se desconectan mientras dura el calculo: el puerto del "
            "panel es uno solo." % (x1 - x0, y1 - y0)),
            style="Chico.TLabel", wraplength=980, justify="left").pack(
            anchor="w", pady=(0, 4))

        b = ttk.Frame(h)
        b.pack(fill="x")
        # Dos filas como en Vivo y Calibrar, por el mismo motivo que ahi: esta
        # fila pedia 1519 px y en una ventana de 1200 los botones de la derecha
        # solo se veian porque el marco los recortaba. Arriba lo que se pulsa,
        # abajo los numeros que se cambian.
        f0 = ttk.Frame(b)
        f0.pack(fill="x")
        f1 = ttk.Frame(b)
        f1.pack(fill="x", pady=(3, 0))
        self.lbl_marcas = ttk.Label(f0, text="", style="Ok.TLabel")
        self.lbl_marcas.pack(side="left")
        boton(f0, "Detectar los dos puntos", self.detectar).pack(
            side="left", padx=(8, 2))
        self.btn_mover = boton(f0, "Mover", self.mover_marca)
        self.btn_mover.pack(side="left", padx=2)
        self.btn_run_marcas = boton(f0, "Detectar y centrar los 2", self.run_marcas)
        self.btn_run_marcas.pack(side="left", padx=(14, 2))
        if not ruida.Panel.AUTOMATIC_MOVE_ENABLED:
            self.btn_run_marcas.configure(state="disabled")
        boton(f0, "Ver coords.txt", self.abrir_coords).pack(side="right")
        boton(f0, "Copiar coordenadas", self.copiar).pack(
            side="right", padx=4)
        self.v_marcas = self._ent(f1, "marcas", 2)
        self.v_it = self._ent(f1, "iteraciones", 4)
        self.v_tol = self._ent(f1, "tolerancia mm", "0.1")
        ttk.Label(f1, text="  ROI mm x0,y0,x1,y1:").pack(side="left", padx=(16, 3))
        self.v_roi = ttk.Entry(f1, width=24)
        self.v_roi.pack(side="left", padx=4)

        # Lo que se copia a mano en LightBurn: la posicion REAL que tiene el
        # cabezal en este punto. Es la que se lee, no la que se calculo, y es
        # por eso que ya no hace falta copiar y pegar nada.
        d = ttk.LabelFrame(h, text="Punto a registrar en LightBurn", padding=6)
        d.pack(fill="x", pady=(6, 0))
        self.lbl_punto = ttk.Label(d, text="sin punto: pulsa Detectar los dos puntos",
                                   font=("Consolas", 14, "bold"))
        self.lbl_punto.pack(anchor="w")
        self.lbl_aviso_marca = ttk.Label(d, text="", style="Aviso.TLabel",
                                         wraplength=960, justify="left")
        self.lbl_aviso_marca.pack(anchor="w")

        cuerpo = ttk.Frame(h)
        cuerpo.pack(fill="both", expand=True, pady=6)
        cuerpo.columnconfigure(0, weight=1)
        cuerpo.columnconfigure(1, weight=0, minsize=360)
        cuerpo.rowconfigure(0, weight=1)
        self.foto_marcas = Foto(cuerpo)
        self.foto_marcas.grid(row=0, column=0, sticky="nsew")
        panel_lateral = ttk.Frame(cuerpo, width=360)
        panel_lateral.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        panel_lateral.grid_propagate(False)
        panel_lateral.columnconfigure(0, weight=1)
        panel_lateral.rowconfigure(0, weight=4)
        panel_lateral.rowconfigure(2, weight=1)
        self.im_marcas_head = self._marco(
            panel_lateral, "CABEZAL (en vivo)", alto=6, celda=(0, 0))
        self.im_marcas_head.master.grid_configure(
            sticky="nsew", pady=(0, 4))
        self.vivos[self.i_marcas] = (None, self.im_marcas_head)
        self._controles_jog(panel_lateral).grid(
            row=1, column=0, sticky="ew", pady=(0, 4))
        self.lst = tk.Listbox(panel_lateral, font=("Consolas", 10), height=3,
                              bg="#101418", fg="#cfd8dc", activestyle="none")
        self.lst.grid(row=2, column=0, sticky="nsew")
        self.lst.bind("<<ListboxSelect>>", self._copia_una)
        ttk.Label(h, text="La foto muestra la deteccion; a la derecha estan el visor "
                          "del cabezal, el jog manual y las coordenadas. Clic en "
                          "una coordenada = copiarla.",
                  style="Chico.TLabel", wraplength=980, justify="left").pack(anchor="w")
        ttk.Label(h, text="El centrado automatico de los dos puntos en una sola tarea "
                          "sigue deshabilitado; usa Mover 1 y Mover 2 por separado.",
                  style="Chico.TLabel", wraplength=980, justify="left").pack(anchor="w")
        ttk.Label(h, text="Mover usa el viaje nativo de LightBurn a coordenadas; "
                          "no simula teclas. El viaje no se puede cancelar desde la "
                          "app: deja libre el recorrido y usa el paro fisico ante "
                          "una emergencia. Cierra LightBurn antes de mover.",
                  style="Aviso.TLabel", wraplength=980, justify="left").pack(anchor="w")
        self.marcas2 = []             # [(mm, mm)] de los dos puntos del material
        self.i_marca = 0              # a cual va el boton Mover

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
        if not ruida.Panel.AUTOMATIC_MOVE_ENABLED:
            self.log("Detectar y centrar suspendido: movimiento automatico "
                     "deshabilitado hasta validar la interpolacion")
            self.lbl_marcas.configure(
                text="centrado automatico temporalmente deshabilitado",
                style="Mal.TLabel")
            return
        self._run(hv.cmd_run, self._ns(marks=int(self.v_marcas.get()),
                                       iters=int(self.v_it.get()),
                                       tol=float(self.v_tol.get().replace(",", ".")),
                                       roi=self.v_roi.get().strip() or None))

    def detectar(self):
        """Paso 1 del flujo: mirar la hoja y quedarse con los dos puntos.

        No mueve el cabezal (park = esquina, las dos camaras ya estan ahi), que
        es lo que hace falta para no perder la referencia de donde se ha
        arrancado. Las manchas fuera del area de trabajo se descartan y no se
        dibujan: solo se pintan las dos que valen."""
        if self._native_move_fault:
            messagebox.showwarning(
                "Movimiento sin confirmar",
                "No se enviaran mas viajes desde esta sesion. Verifica que el "
                "cabezal este detenido y reinicia la app antes de volver a mover.",
                parent=self)
            return
        self.marcas2, self.i_marca = [], 0
        self.lbl_marcas.configure(text="buscando las manchas...", style="Chico.TLabel")
        self.lbl_punto.configure(text="sin punto: pulsa Detectar los dos puntos",
                                 foreground="#8b1a1a")
        self.lbl_aviso_marca.configure(text="")
        self._boton_mover()
        self.log("el cabezal tiene que estar en una esquina (Origen 0,0) para "
                 "que el detector no vea el cabezal encima de una marca")
        self._run(hv.cmd_run, self._ns(no_move=True))

    def _boton_mover(self):
        """El boton que cambia solo: Detectar, luego Mover 1, luego Mover 2, y
        vuelta a empezar. Que se lea lo que toca evita el paso de "¿ahora que
        botón era?"."""
        if self._native_move_fault:
            self.btn_mover.configure(text="Mover bloqueado", state="disabled")
            self.lbl_aviso_marca.configure(
                text="Movimiento no confirmado: verifica que el cabezal se "
                     "detuvo y reinicia la app antes de otra prueba.")
            return
        if self._native_move_active:
            self.btn_mover.configure(text="Viaje en curso...", state="disabled")
            return
        if not self.marcas2:
            self.btn_mover.configure(text="Mover", state="disabled")
            return
        if not ruida.NATIVE_POSITION_MOVE_ENABLED:
            self.btn_mover.configure(text="Mover (suspendido)", state="disabled")
            self.lbl_aviso_marca.configure(
                text="El viaje nativo a coordenadas esta deshabilitado.")
            return
        i = self.i_marca
        self.btn_mover.configure(
            text="Mover %d" % (i + 1) if i < len(self.marcas2) else "Mover",
            state="normal")

    def _boton_origen(self):
        if not ruida.NATIVE_POSITION_MOVE_ENABLED or self._native_move_fault:
            self.btn_origen.configure(state="disabled")
        else:
            self.btn_origen.configure(
                state="disabled" if self._native_move_active else "normal")

    def mover_marca(self):
        """Pasos 3 y 4: llevar el cabezal al punto 1 y luego al punto 2."""
        if self._native_move_fault:
            self.log("Mover bloqueado: primero verifica la posicion fisica")
            return
        if self._jog_en_curso():
            self.log("Mover bloqueado: suelta primero el control de jog")
            return
        if not ruida.NATIVE_POSITION_MOVE_ENABLED:
            self.log("Mover ignorado: viaje nativo a coordenadas deshabilitado")
            return
        if not self.marcas2:
            self.log("aun no hay puntos: pulsa Detectar los dos puntos")
            return
        i = self.i_marca
        if i >= len(self.marcas2):
            self.detectar()
            return
        mm = self.marcas2[i]
        self._native_move_active = True
        self._boton_origen()
        self.btn_mover.configure(text="Moviendo %d..." % (i + 1), state="disabled")
        self.lbl_marcas.configure(text="moviendo al punto %d..." % (i + 1),
                                  style="Chico.TLabel")
        self.log("marcando punto %d: %.3f, %.3f mm" % (i + 1, mm[0], mm[1]))
        # El leer los campos desde el hilo de trabajo no es seguro en Tk, asi que
        # se leen aqui (hilo de la interfaz) y se pasan ya convertidos.
        ns = self._ns(iters=int(self.v_it.get()),
                      tol=float(self.v_tol.get().replace(",", ".")))
        # _tarea solo pasa el future a al_terminar, asi que el position va dentro
        self._tarea(self._marca_a, mm, i, ns,
                    al_terminar=lambda fu: self._fin_movimiento(i, fu))

    def _fin_movimiento(self, i, fu):
        self._native_move_active = False
        try:
            pos, problema = fu.result()
        except Exception as e:
            self._native_move_fault = True
            detalle = "ERROR al mover al punto %d: %s" % (i + 1, e)
            self.log(detalle)
            self.lbl_marcas.configure(text=detalle, style="Mal.TLabel")
            self.lbl_punto.configure(text="PUNTO %d: MOVIMIENTO CANCELADO"
                                      % (i + 1), foreground="#8b1a1a")
            self._boton_mover()
            self._boton_origen()
            messagebox.showerror(
                "Movimiento no confirmado",
                "%s\n\nEl viaje nativo no se puede cancelar desde la app. "
                "Verifica la posicion fisica antes de volver a mover."
                % detalle, parent=self)
            return
        self._fin_punto(i, pos, problema)

    def _foto_cabeza(self, timeout=5.0):
        """Un fotograma NUEVO de la camara del cabezal, en gris, ya publicado
        por el hilo de preview.

        No se abre la camara aqui: la tiene abierta ese hilo y abrirla dos veces
        el mismo indice da imagen negra o falla. Se espera a que el contador de
        fotogramas avance para no medir un fotograma de antes del movimiento. Y
        si el hilo de las camaras ha muerto, el contador se queda quieto y esto
        avisa en vez de medir negro.

        ponytail: el preview lee UN fotograma por vuelta y sin el Laplacian de
        `grab`, asi que este frame puede ir desenfocado. Si el centrado no
        converge por eso, parar el preview y abrir la camara con
        `hv.grab(cap, n)` como hace cmd_run.
        """
        v = self.video
        if v is None:
            raise RuntimeError("no hay camaras conectadas: pulsa Conectar camaras")
        n0 = v.frame()[0]
        limite = time.time() + timeout
        while time.time() < limite:
            n, _, head = v.frame()
            if n != n0 and head is not None:
                return head
            time.sleep(0.02)
        raise RuntimeError("la camara del cabezal no da fotogramas nuevos: "
                           "mira si el hilo de las camaras sigue vivo")

    def _marca_a(self, mm, i, ns):
        """En el hilo de trabajo: el cabezal al punto, el centrado fino con la
        camara del cabezal, y la posicion que de verdad tiene. Es la que se
        anota a mano en LightBurn, asi que si no queda centrado no se enseña
        ninguna: una posicion sin centrar dispara donde no toca."""
        pan = self.maq.get()
        if pan is None or pan.pan is None:
            self.log("sin panel: no se puede mover")
            return None, "sin panel: no se puede mover"
        pos = pan.goto_native(mm[0], mm[1])
        if not _posicion_valida(pos):
            raise RuntimeError("el panel no devolvio una posicion valida al mover")
        error = float(np.hypot(pos[0] - mm[0], pos[1] - mm[1]))
        if error > 0.5:
            raise RuntimeError(
                "no se alcanzo el punto %.3f, %.3f; el cabezal quedo en "
                "%.3f, %.3f (error %.3f mm)"
                % (mm[0], mm[1], pos[0], pos[1], error))
        self.log("punto alcanzado: %.3f, %.3f mm; centrando" % (pos[0], pos[1]))
        return hv.fine(pan, self._foto_cabeza, self.cfg, ns)

    def _ir_origen_nativo(self):
        maquina = self.maq.get()
        if maquina is None:
            raise RuntimeError("no se pudo abrir el panel de la Ruida")
        pos = maquina.goto_native(0.0, 0.0)
        if not _posicion_valida(pos) or float(np.hypot(*pos)) > 0.5:
            raise RuntimeError("el viaje al origen no se confirmo: %s" % (pos,))
        return pos

    def ir_a(self, x, y):
        if (x, y) != (0.0, 0.0):
            self.log("Origen 0,0: destino no permitido por este control")
            return
        if not ruida.NATIVE_POSITION_MOVE_ENABLED:
            self.log("Origen 0,0: viaje nativo deshabilitado")
            return
        if self._native_move_active or self._native_move_fault:
            self.log("Origen 0,0 bloqueado: hay un viaje activo o sin confirmar")
            return
        if self._jog_en_curso():
            self.log("Origen 0,0 bloqueado: suelta primero el control de jog")
            return
        self._native_move_active = True
        self._boton_origen()
        self._boton_mover()
        self.log("viaje nativo al origen 0.000, 0.000 mm (no cancelable desde la app)")
        self._tarea(self._ir_origen_nativo, al_terminar=self._fin_origen)

    def _fin_origen(self, fu):
        self._native_move_active = False
        try:
            pos = fu.result()
        except Exception as e:
            self._native_move_fault = True
            detalle = "ERROR al mover al origen: %s" % e
            self.log(detalle)
            self.lbl_pos.configure(text=detalle, style="Mal.TLabel")
            self._boton_origen()
            self._boton_mover()
            messagebox.showerror(
                "Origen no confirmado",
                "%s\n\nEl viaje nativo no se puede cancelar desde la app. "
                "Verifica la posicion fisica antes de volver a mover." % detalle,
                parent=self)
            return
        self.pos, self._ok = pos, True
        self.lbl_pos.configure(text="origen confirmado: X = %.3f  Y = %.3f mm" % pos,
                                style="Ok.TLabel")
        self.log("origen confirmado: %.3f, %.3f mm" % pos)
        self._boton_origen()
        self._boton_mover()

    def _fin_punto(self, i, pos, problema=None):
        """Ya esta el cabezal en el punto: se enseña la posicion para copiarla
        a LightBurn con un dedo, no con el portapapeles. Si el centrado no ha
        funcionado no se enseña ninguna posicion: el boton se queda en este
        punto para poder reintentarlo y el motivo va en el rotulo, que es donde
        se mira cuando algo no cuadra."""
        ox, oy = self.cfg["cam_offset_mm"]
        if problema:
            self.lbl_marcas.configure(text="punto %d sin centrar" % (i + 1),
                                      style="Mal.TLabel")
            self.lbl_punto.configure(text="PUNTO %d: SIN CENTRAR" % (i + 1),
                                      foreground="#8b1a1a")
            self.lbl_aviso_marca.configure(
                text="%s. El cabezal esta en el punto, pero esa posicion no vale "
                     "para LightBurn: arregla lo de arriba y pulsa Mover %d otra vez."
                     % (problema, i + 1))
            self._boton_mover()
            self._boton_origen()
            return
        if not _posicion_valida(pos):
            self.lbl_punto.configure(
                text="PUNTO %d: movimiento no confirmado; mira el registro"
                     % (i + 1), foreground="#8b1a1a")
            self._boton_mover()
            return
        self.i_marca = i + 1
        self._boton_mover()
        self._boton_origen()
        x, y = (pos[0] + ox, pos[1] + oy) if i == 0 else (pos[0], pos[1])
        self.lbl_punto.configure(text="PUNTO %d:    X = %.3f     Y = %.3f mm"
                                  % (i + 1, x, y), foreground="#0a7d33")
        self.lbl_marcas.configure(text="punto %d listo" % (i + 1), style="Ok.TLabel")
        self.lbl_aviso_marca.configure(
            text=("Anota el X y la Y de arriba en LightBurn > Print and Cut como "
                  "punto %d." % (i + 1)) if i == 0 else
                  "Anota el X y la Y de arriba como SEGUNDO punto y pon en LightBurn "
                  "el offset de la camara DESACTIVADO (offset = 0): el desplazamiento "
                  "ya esta calculado aqui y con offset activado se cuenta dos veces.")

    def _run(self, fn, ns):
        self._volvio = bool(self.video)
        if self.video:
            self.log("se desconectan las camaras: el calculo abre las suyas")
        self.parar_cams()
        # El panel de la Ruida escucha en un puerto FIJO (40207) y el run abre el
        # suyo: dos sockets en el mismo puerto dan [WinError 10048] y el run se
        # cae sin escribir coords.txt ("sin coordenadas: mira el registro"). Se
        # suelta aqui y `Maquina.get()` lo vuelve a abrir cuando haga falta.
        if self.maq.mq:
            self.log("se suelta el panel del cabezal: el puerto es uno solo")
            self.maq.cerrar()
        self.lst.delete(0, "end")
        self.coords = []
        self._tarea(fn, ns, al_terminar=self._fin_marcas)

    def _elige_marcas(self, found):
        """Las manchas que valen, en mm, y un aviso si no son las pedidas.

        El criterio es el de hybrid_vision, no uno proprio: fuera del area de
        trabajo se descarta ANTES de elegir la pareja con los componentes mas
        grandes, para que un reflejo pequeno no gane por estar mas lejos. Los px
        que se pintan se dejan en `self.marcas_utiles` para el lienzo."""
        H = self.cfg.get("H")
        n = int(self.v_marcas.get() or 2)
        if not H:
            self.marcas_utiles = []
            return [], "sin homografia: primero calibra"
        t = self.v_roi.get().strip()
        roi = [float(v) for v in t.replace(";", ",").split(",") if v] if t else None
        dentro, fuera = hv.marcas_utiles(found, np.array(H, np.float32), roi)
        if fuera:
            self.log("descartadas %d manchas fuera del area de trabajo %s"
                     % (len(fuera), roi or hv.area_trabajo()))
        if len(dentro) >= n:
            # pick_pair devuelve la PAREJA (a, b), no una marca: envolverlo en
            # lista dejaba una tupla dentro y al desempaquetar petaba.
            elegidas = list(hv.pick_pair(dentro, found)) if n == 2 else dentro[:n]
            self.marcas_utiles = [p for _, p in elegidas]
            return [mm for mm, _ in elegidas], (
                "%d manchas fuera del area de trabajo: descartadas" % len(fuera)
                if fuera else "")
        self.marcas_utiles = [p for _, p in dentro]
        return [], "solo %d manchas de las %d dentro del area de trabajo: %s" % (
            len(dentro), n, "acota el ROI" if not fuera else "las demas estan fuera")

    def _fin_marcas(self, fu):
        """Muestra la misma pareja de píxeles que produjo coords.txt."""
        fallo = ""
        try:
            marks = fu.result()
            if marks is None:
                raise RuntimeError("la deteccion no devolvio las marcas seleccionadas")
            marks = list(marks)
            if any(len(mark) != 2 or len(mark[0]) != 2 or len(mark[1]) != 2
                   for mark in marks):
                raise ValueError("formato inesperado de marcas seleccionadas")
            self.marcas2 = [tuple(map(float, mm)) for mm, _ in marks]
            self.marcas_utiles = [tuple(map(float, px)) for _, px in marks]
        except (Exception, SystemExit) as e:
            fallo = "ERROR al detectar marcas: %s" % e
            self.marcas2, self.marcas_utiles = [], []
            self.log(fallo)

        g = cv2.imread(hv.BED_PNG, cv2.IMREAD_GRAYSCALE) \
            if os.path.exists(hv.BED_PNG) else None
        if fallo:
            self.lbl_marcas.configure(text=fallo, style="Mal.TLabel")
            self.lbl_punto.configure(text="deteccion cancelada; mira el registro",
                                     foreground="#8b1a1a")
        elif len(self.marcas2) >= 2:
            self.lbl_marcas.configure(
                text="puntos detectados:  %s mm"
                     % "   ".join("%.2f, %.2f" % mm for mm in self.marcas2),
                style="Ok.TLabel")
            self.lbl_punto.configure(
                text="puntos detectados: pulsa Mover 1 para viaje nativo Ruida",
                foreground="#0a7d33")
        else:
            self.lbl_marcas.configure(
                text="solo se seleccionaron %d marcas; se necesitan 2"
                     % len(self.marcas2), style="Mal.TLabel")
            self.lbl_punto.configure(text="deteccion incompleta; mira el registro",
                                     foreground="#8b1a1a")
        self._boton_mover()
        if g is not None:
            self.foto_marcas.poner(g, self.marcas_utiles)
            self.log("marcas dibujadas en visor (%d): %s" %
                     (len(self.marcas_utiles),
                      [(round(u, 1), round(v, 1))
                       for u, v in self.marcas_utiles]))
        if not fallo and os.path.exists(COORDS):
            self.coords = [l.strip() for l in
                           open(COORDS, encoding="utf-8").read().splitlines() if l.strip()]
            self.lst.delete(0, "end")
            for l in self.coords:
                self.lst.insert("end", l)
        elif not fallo:
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
        boton(f, "Guardar ajustes", self.guardar_cfg).grid(row=9, column=1,
                                                                    sticky="w", pady=10)
        boton(f, "Listar camaras", self.scan_cams).grid(row=9, column=0,
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
                elif clave == "head_fov_mm":
                    # float y no texto: hybrid_vision multiplica por este valor
                    v = float(t.replace(",", "."))
                else:
                    v = t
            except ValueError:
                self.log("Ajustes: %s no vale (%r), se deja como estaba" % (clave, t))
                continue
            cfg[clave] = v
        hv.save_cfg(cfg)
        self.cfg = cfg
        self._fov_muestra()                # las dos pestañas muestran lo mismo
        # Maquina.get() cachea hv.Machine con la config de cuando se creo, y
        # solo lo rehace si cambia la IP: con el coche cacheado, Guardar un
        # estacionamiento nuevo no movia nada, iba al de antes.
        self.maq.cerrar()
        self.sentido()
        self.log("ajustes guardados en %s" % hv.CFG)

    def scan_cams(self):
        self.parar_cams()               # el scan abre todos los indices
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
                    vis = self.vivos.get(self.hojas.index("current"))
                    if vis:
                        if vis[0] is not None:
                            self._foto(vis[0], self._con_top(top))
                        if vis[1] is not None:
                            self._foto(vis[1], self._con_head(head))
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
        cv2.rectangle(im, (x0, y0), (x1, y1), (0, 220, 0), 2)
        cv2.line(im, (w // 2 - 36, h // 2), (w // 2 + 36, h // 2),
                 (0, 255, 0), 2)
        cv2.line(im, (w // 2, h // 2 - 36), (w // 2, h // 2 + 36),
                 (0, 255, 0), 2)
        cv2.circle(im, (w // 2, h // 2), 7, (0, 255, 255), 2)
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
            self.lbl_pos.configure(text="posicion:  X = %8.3f    Y = %8.3f mm" % p,
                                   style="Ok.TLabel")
            self.lbl_pan.configure(text="panel %s: contesta" % self.cfg["ip"],
                                   style="Ok.TLabel")
        else:
            self.pos, self._ok = None, False
            self.lbl_pos.configure(text="posicion: el panel %s no contesta"
                                   % self.cfg["ip"], style="Mal.TLabel")
            self.lbl_pan.configure(text="panel %s: SIN RESPUESTA" % self.cfg["ip"],
                                   style="Mal.TLabel")

    # -------------------------------------------------------------------- OTA
    def ota(self):
        self.log("se mira si hay version nueva")
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
        # en %TEMP% y con el nombre que trae la release. Antes se armaba la ruta
        # con os.path.dirname del nombre, que en un nombre suelto es "" y dejaba
        # una ruta relativa: la descarga fallaba y la app se cerraba igual, sin
        # instalar nada (que es justo lo que se quejaba).
        ruta = os.path.join(tempfile.gettempdir(), a["name"])
        self.lbl_ota.configure(text="descargando %.0f MB..."
                               % (a.get("size", 0) / 1048576.0))
        self._tarea(self._baja, a["browser_download_url"], ruta,
                    al_terminar=self._ota_lanza)

    def _baja(self, url, ruta):
        for tot in actualizar.descargar(url, ruta):
            if tot is None:
                break
            self.log("descargados %.0f MB" % (tot / 1048576.0))
        # Ruta y tamano antes de lanzar: si el fichero esta a medias o a cero, el
        # registro dice exactamente que se descargó, que es el dato que hace
        # falta para saber si el problema es la red o el arranque.
        self.log("instalador en %s (%d bytes), a cerrar la app para ejecutarlo"
                 % (ruta, os.path.getsize(ruta) if os.path.exists(ruta) else 0))
        actualizar.lanzar(ruta)
        return True

    def _ota_lanza(self, fu):
        """Cierra la app SOLO si la descarga y el lanzamiento salieron bien."""
        try:
            if not fu.result():
                raise IOError("no se pudo instalar")
        except Exception as e:
            self.lbl_ota.configure(text="fallo la actualizacion: %s" % e)
            messagebox.showerror("Actualizacion",
                                 "No se pudo instalar la actualizacion:\n\n%s\n\n"
                                 "La app sigue como estaba." % e)
            return
        self.salir()

    # ----------------------------------------------------------------- salida
    def abrir_log(self):
        if not os.path.exists(LOGF):
            self.log("todavia no hay app.log")
            return
        os.startfile(LOGF)

    def salir(self):
        if self._native_move_active:
            messagebox.showwarning(
                "Viaje nativo en curso",
                "Espera a que el cabezal llegue al destino antes de cerrar. "
                "La app no puede cancelar este viaje; ante una emergencia usa "
                "el paro fisico de la controladora.",
                parent=self)
            return
        if self._native_move_fault and not messagebox.askyesno(
                "Movimiento sin confirmar",
                "Confirma que el cabezal esta fisicamente detenido antes de "
                "cerrar la app.",
                parent=self):
            return
        # sin `after` que llegue a correr: cortar aqui mismo y en sincrono
        for t in (self._jog_t, self._jog_p):
            if t:
                self.after_cancel(t)
        self._jog_t = self._jog_p = None
        if self._jog_ev is not None:
            self._jog_ev.set()
            self._jog_ev = None
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
