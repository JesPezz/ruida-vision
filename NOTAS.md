# Notas para la próxima sesión

Estado: **v1.7, la v1.6 más los siete ajustes que salieron de usarla en la máquina.**
Todo lo de abajo está verificado (`py -u hybrid_vision.py test`, `python ruida.py test`,
`/root/venv/bin/python -m ruidavision.actualizar test`, y
`/root/venv/bin/python -m ruidavision.prueba_app` → 0 fallos de 61 comprobaciones).
Lo que queda es lo que solo se puede comprobar con la máquina delante, y está al final.

## 0. Lo de esta versión (v1.7)

1. **El paso es un número en mm, no un desplegable** (`self.paso_mm`, `lbl_paso`,
   `_pon_paso`, `_cambia_paso`): `-` y `+` lo mueven en saltos de 0,1 mm entre
   `PASO_MIN=0.1` y `PASO_MAX=10.0`. El pulsado lo traduce `ms_de_paso()` en
   hybrid_vision.py, que interpola los tres puntos medidos (0,2/0,44/3,4 mm),
   extrapola con la pendiente del último tramo y nunca baja de 1 ms: por debajo la
   Ruida no distingue un pulso de un keyup, así que un paso más fino es
   indistinguible de "no mover". El `jog` de la CLI **no** cambia: sigue con su
   ciclo de tres pasos, es otro uso.
2. **Botones con icono y leyenda flotante** (`ToolTip` + `boton()`). La leyenda es
   un `tk.Label` que se coloca con `place` a los 600 ms de parar el ratón, y se
   esconde con `place_forget()`. Ojo: `withdraw()`/`deiconify()` son de `Wm`, y un
   Label no es toplevel — con `withdraw()` reventaba al construir la app.
3. **La botonera del log va en `grid`**, no en un `pack` encadenado: los botones de
   arriba del registro ya no se comían la última línea del log.
4. **Calibrar reparte los tres visores** (`_marco` con `weight`): las dos cámaras
   en vivo en la fila de arriba, la foto congelada debajo y a todo lo ancho.
   Medido en la prueba: vivos 381×388 y 388×388, congelada 809×212.
5. **Filtro de marcas común a la app y a la CLI** (`area_trabajo` y
   `marcas_utiles` en hybrid_vision.py). Fuera del área de trabajo (500×400 mm) no
   son marcas: son tags o reflejos, y además su mm viene de una homografía
   extrapolada (un tag en el borde daba 599 mm en una cama de 500×400). Se
   descartan **antes** de elegir la pareja; a quien le pase le avisa de cuántas se
   dejaron fuera. `cmd_run` y la hoja Marcas usan el mismo helper: el criterio no
   puede ser distinto según por dónde se entre.
6. **Print and Cut sin portapapeles** (`detectar` → `mover_marca` → `_fin_punto`):
   `detectar` no mueve el cabezal, deja los dos puntos a la vista; `➜` va al
   punto 1, se lee la posición en grande y se anota a mano en LightBurn; `➜` otra
   vez va al punto 2 y avisa de que el offset de LightBurn debe quedar
   desactivado. El mismo botón va cambiando de texto ("Mover 1" → "Mover 2" → "➜").
   La posición que se enseña es la del cabezal **más** `cam_offset_mm` en el punto
   1 (donde se pulsa el botón en la app) y la de la máquina en el punto 2 (donde se
   anota en LightBurn).
7. **El instalador se ejecuta** (`actualizar.lanzar` + `_orden`): `cmd /c ping -n 4
   127.0.0.1 >nul && "inst.exe" /CLOSEAPPLICATIONS`, con `DETACHED_PROCESS |
   CREATE_NO_WINDOW` y `close_fds=True`. Sin despegarse del proceso no se puede
   cerrar la app antes de ejecutar. Antes de lanzar se registra ruta y tamaño del
   `.exe`, que es el dato que separa "falló la red" de "falló el arranque".
   `lanzar` avisa y no hace nada si el fichero no está.

### Bugs que salieron al probar esto

- `pick_pair` devuelve la **pareja** `(a, b)`. Envolverla en lista dejaba una tupla
  dentro y petaba al desempaquetar. Lo caza la prueba de la app.
- `boton()` con `width` explícito reventaba por `multiple values for keyword`;
  ahora el `width=3` por defecto se pisa con `dict({"width": 3}, **kw)`.
- `ToolTip` con `withdraw()`: método de `Wm`, no existe en `Label`.
- `os.path.getsize(ruta)` en el log de la descarga reventaba si el fichero no llegó
  a crearse; ahora se registra 0 bytes en vez de morir.

## 1. Lo de la v1.6

1. **`-` y `+` cambian el paso del toque** (`_cambia_paso`, tabla `PASOS` en app.py),
   sin soltar el WASD. En la v1.7 el desplegable desaparece y el paso pasa a mm.
2. **Calibrar con los tres visores a la vez**: `self.vivos = {hoja: (cenital, cabezal)}`
   y `_pintar` pinta solo los de la hoja visible. La foto congelada (`self.foto`) es un
   `Foto` aparte, debajo. Se elimina `_visor_cal` y con ella `hay_foto` (ya no lo leía
   nadie: los dos visores ya no se pisan nunca).
3. **Marcas enseña la foto de la cama** (`self.foto_marcas`) con las manchas
   re-marcadas por el mismo `find_marks` que el run usó, al lado de la lista de
   coordenadas, y dos líneas de explicación. `bed.png` se relee con
   `IMREAD_GRAYSCALE`: el detector y el lienzo quieren gris, no BGR.
4. **`WinError 10048` al ejecutar Marcas — ARREGLADO.** Causa: `ruida.Panel.__init__`
   hace `bind(("0.0.0.0", SRC_PANEL))` con `SRC_PANEL = 40207` **fijo**, y la app
   tenía su panel vivo (para el jog) mientras `cmd_run` abría el suyo. El `bind`
   reventaba, el run moría sin escribir `coords.txt` y la pestaña se quedaba en
   "sin coordenadas: mira el registro". Ahora `_run` (y `scan_cams`) sueltan
   `self.maq` antes de lanzar el cálculo. **No** se pasa a puerto efímero (el panel
   manda los informes de posición a un puerto fijo) ni se pone `SO_REUSEADDR` (el
   kernel repartiría los datagramas entre los dos sockets).
5. **El "Error de reproyeccion" de la v1.5 era un susto con nombre.** Era el residuo
   del ajuste de homografía impreso sin contexto: 0,202 mm máximo / 0,082 medio es
   excelente. Ahora dice "residuo del ajuste" y aclara que 0,1–0,2 mm es normal.

## 0-bis. Lo de la v1.5

1. **El OTA no descargaba.** La ruta de destino era relativa al `cwd` del ejecutable
   (bajo `Program Files` no hay permiso) y la app se cerraba igual. Ahora: descarga a
   `%TEMP%` con ruta absoluta, y solo se cierra si el fichero se ha podido abrir.
2. **Cámaras lentas.** No era hardware, era `cap.set(CAP_PROP_FOURCC)` después de
   abrir: el driver se queda en YUY2 (3,7 MB/fotograma a 1080p). Medido con la cenital
   de esta máquina: 8,4 s al primer fotograma y 2,2 fps así; pidiendo MJPG + resolución
   + fps en el constructor, 0,9 s hasta abrir, 0,03 s al primer fotograma y 24,9 fps.
   La cenital **entrega 1280x720 aunque se le pida 1920x1080** (avisa y sigue con la
   real, que la homografía se escala por `frame/cal`).
3. **Origen a tirones.** `move_to` era todo pulsos de 100 ms (3,4 mm) con lectura de
   posición en medio: una rampa de aceleración por cada 3,4 mm. Ahora jiro continuo a
   ~5 mm/s con corte por destino (`jog_hold(corte=...)` + `_ir_hacia`), y el paso fino
   se queda para el último milímetro.
4. **Calibrar: cámara y foto.** Al entrar en la pestaña se ve la cámara en directo, el
   botón de foto es un icono de cámara, y con una captura ya congelada el visor ya no
   se pisa: el botón pasa a guardar. `hay_foto` es lo que decide.
5. **Numeración de las manchas.** La columna "n" manda: `cal_ajusta` empareja por
   número (antes por orden de lista, con las manchas cambiadas de sitio), hay
   "Cambiar n°" para renumerar a mano, "Renumerar" ordena por número y renumera 1..N,
   y el número nuevo es el libre más bajo. Da igual en qué orden se apunten las 70
   manchas.

## 1. Se movía a la inversa — ARREGLADO

Causa: `LETRAS` estaba escrito a mano desde coordenadas de máquina. El origen de la
máquina está en la esquina inferior izquierda, pero eso no dice nada de cómo está
montada la cenital: con la cámara girada 180° la Y de la máquina cae hacia abajo en la
imagen. Por eso el usuario decía "a la inversa" y no "un eje va mal": **los dos**.

Arreglo: el mapeo **se deduce de la homografía** (`_wasd_de_H`), preguntando a `H` por
dónde caen "derecha" (cx+50) y "arriba" (cy-50) en la imagen y tomando el signo. Con la
`H` de `calib.json` actual salen `d:-X` y `w:-Y`, o sea los dos invertidos: confirma el
síntoma y la deducción lo corrige sola, sin tocar nada a mano.

- Las flechas NO se voltean: dependen solo del teclado, no de la cámara.
- Sin `H` todavía (la primera calibración) se usa el mapeo de por defecto, y
  `--flip-mov` lo da la vuelta a mano (bit 1 = derecha/izquierda, bit 2 = arriba/abajo,
  `--flip-mov` alterna los dos) para desempatar si aun no hay homografía.
- En pantalla: una **flecha por letra** en la cenital, con su etiqueta, de 8 mm, desde
  el círculo rojo del cabezal. Se ve de un vistazo si W sale hacia abajo.

Test: `Maq` con una cenital bien montada da el mapeo de siempre; `MaqGirada` (H girada
180°) exige `[("-Y",20),("+X",20),("+Y",20),("-X",20)]` — el inverso exacto.

## 2. Lag y cuelgues — ARREGLADO (a y b)

**(a) El Laplacian en el lazo de navegación.** `mirar` ya no usa `grab()`: usa el nuevo
`read_gris()` (un `read()` + gris, sin elegir nitidez) en las dos cámaras. Eran 3
fotogramas con Laplaciano `CV_64F` por cámara y por vuelta, ~16 MB de ida y vuelta cada
uno. `grab(n=3)` se queda donde sí importa: `click_point`, `cmd_fov`, `cmd_run`, `fine()`.

**(b) `m.pos()` bloqueante.** La posición se lee a 2 Hz (`m.pos(0.6)`) y el resto de la
vuelta se dibuja con la última conocida. Además ahora se distingue en pantalla entre
posición leída, "posicion: leyendo..." y **"SIN RESPUESTA DEL PANEL (50207)"** cuando el
handshake falló (`Machine.ok`).

**(c) `Panel.hold` con `time.sleep` (ruida.py:299) — ARREGLADO en la GUI.** `hold` sigue
igual (la CLI no lo sufre), pero la app ya no manda pulsos sueltos: en `ruidavision/app.py`
cada tarea de máquina corre en un `ThreadPoolExecutor(max_workers=1)` y el jog usa el nuevo
`Panel.jog_hold` (un solo *keydown*, bucle leyendo posición, *keyup* en el `finally`).
Mantener la tecla o el botón mueve en continuo; un toque corto da un paso fino.

## 3. La ventana no se redimensionaba — ARREGLADO

`cv2.namedWindow(win, cv2.WINDOW_NORMAL)` + un `imshow` de tamaño fijo cada vuelta hace
que el backend de Windows reajuste la ventana y pelee con el usuario. En `mirar` y en
`cmd_cams` ahora es `cv2.namedWindow(win)` (autosize), el mismo patrón que usa
`click_point`, que nunca dio guerra. El contenido ya se reduce a 720 px de alto, así que
cabe en pantalla.

## 4. Interfaz poco amigable — ARREGLADO

- Franja negra con **CABEZAL** y **CENITAL** en cada mitad (`_barra`).
- **"PUNTO 3/6"** en la ventana, no solo en el título.
- Estado del panel en pantalla: posición en mm, "leyendo..." o "SIN RESPUESTA".
- **"paso 0.2 mm"** visible, y el **mapeo deducido impreso por consola** al abrir cada
  punto (con W/A/S/D y de dónde sale).
- Flechas de sentido en la cenital (ver punto 1).
- Ayuda y textos con sombra negra (`_texto`) y escala 0.6, para que se lean.

## 5. Pendiente de máquina (no se puede comprobar sin ella)

Lo de la v1.7, en este orden:

- **El flujo de Print and Cut, a pelo**: centrar la plantilla, `◎ Detectar los dos
  puntos`, `➜` al punto 1, anotar en LightBurn, `➜` al punto 2, anotar. La posición
  que sale en grande tiene que coincidir con la que marca el cabezal ahí. Luego:
  en LightBurn, offset **desactivado**.
- **`-` y `+` con el cabezal en la mano**: 0,1 mm se nota como un roce, 10 mm se
  nota de golpe, y ni un paso se queda sin mover.
- **El instalador de verdad**: Ajustes → Actualizar, y comprobar que la app se
  cierra, aparece el instalador y arranca. Antes solo se descargaba.
- Las leyendas de los botones: que se lean y que no se queden puestas al mover el
  ratón a otro botón.

Lo de antes, igual:

- `py hybrid_vision.py calibrate --park 20,20` y mirar que **W va hacia arriba** en la
  cenital. Con la `H` actual tiene que salir así solo; si no, `--flip-mov`.
- `run --iters 1` con el **láser apagado** para comprobar los signos `head_flip_x`/`y`.
- El punto malo (px(541,46)=mm(0,0)) sigue en `cfg["points"]`; es cosmético, `run` solo
  usa `H` y `calibrate` sobrescribe `points`.

## 6. La app GUI, el instalador y el OTA — HECHO

`ruidavision/` tiene la app Tkinter (pestañas Vivo / Calibrar / Marcas / Ajustes),
`RuidaVision.spec` + `build_windows.bat` (PyInstaller) + `installer/RuidaVision.iss`
(Inno Setup 6), y `ruidavision/actualizar.py` (OTA contra `releases/latest` de GitHub).

Queda por hacer / comprobar:
- El techo de **5 mm/s es del perfil de LightBurn** ("Config maquina jog lento"); no se
  puede cambiar desde aquí porque `set_param` no hace nada (README 319-323).

El **repo es público** a propósito: `actualizar.py` no lleva token (solo biblioteca
estándar, a propósito), y contra un repo privado la API devuelve 404 aunque exista la
release. Con el repo público, `releases/latest` responde sin autenticar y el instalador
se descarga sin sesión.

## Cómo probar

```
py hybrid_vision.py test                                  # deteccion, ROI, filtro, paso y teclado, sin máquina
python ruida.py test                                      # protocolo del panel, sin máquina
py hybrid_vision.py calibrate --park 20,20                # ventana con las 2 cámaras

# App de escritorio (en la Pi hace falta xvfb-run: no tiene escritorio real)
/root/venv/bin/python -m ruidavision.prueba_app           # 61 comprobaciones de GUI y jog
/root/venv/bin/python -m ruidavision.actualizar test      # 13 del descargador y el arranque
```

`prueba_app.py` falsea `hv.save_cfg` de **toda** la corrida: antes, una prueba que se
pasaba guardaba una `H` de mentira en el `calib.json` de la máquina y la siguiente
reventaba con `Singular matrix`. Ya pasó una vez y se restauró con
`git checkout -- calib.json`. Si vuelve a salir un `Singular matrix` al arrancar, mira
`det(H)` de `calib.json` antes de tocar nada más.

W A S D mueven, un paso por pulsación; `v` cicla el paso entre 0.2 / 0.44 / 3.4 mm; Enter
acepta el punto; `q` sale. Si sale `tecla 0x... sin asignar`, ese es el código crudo de
una tecla que falta en la tabla.

Espejo de trabajo: la **Raspberry Pi** `root@192.168.1.107:/root/ruida-vision` (mismo
commit que el PC). Para probar la GUI hay que usar `/root/venv/bin/python` (el `python3`
del sistema no tiene `cv2`) y `xvfb-run -a` (la Pi no tiene escritorio). El espejo viejo
del PC (`/root/ruida-vision`) se borró: era el commit base, sin los arreglos.
