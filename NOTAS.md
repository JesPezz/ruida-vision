# Notas para la próxima sesión

Estado: **v2.0, que es la v1.9 con los viajes nativos de LightBurn en Print and Cut,
la hoja de Marcas desplazable, el jog compacto, el estado en color, el registro con
barra, los textos que siguen el ancho, el escalado de Windows y las barras de botones
que ya no se salen.** Verificado con `py -u hybrid_vision.py test`, `python ruida.py
test`, `python -m ruidavision.actualizar test` y `python -m ruidavision.prueba_app`
→ 0 fallos. Lo que queda es lo que solo se puede comprobar con la máquina delante, y
está al final. **El diario de las pruebas con la máquina es `coplitovs-notas.md`:
si algo de §5 se contradice con ese fichero, el fichero manda.**

## 0. Lo de esta versión (v2.0)

Nota de numeración: esta entrega se publicó como **v1.10** por error; el número que
le tocaba, el siguiente a la v1.9, es **2.0**. La release `v1.10` queda como está
(no se puede renumerar) y el próximo build ya sale con `VERSION = "2.0"`. El
comparador del actualizador usa tuplas de enteros, así que quien tenga la 1.10 verá
la 2.0 como nueva (`actualizar.py test` lo comprueba).

0. **El panel ya no se pisa a sí mismo (carrera en el socket de posición).** El
   `Panel` es uno solo y su socket lo tocan a la vez el poll de posición a 3 Hz de la
   app (`app.py:_cada_paso`) y el hilo del viaje nativo, el jog y el `Parar`. Todo
   eso corre en el pool de hilos, así que el "nunca desde la UI" del comentario era
   cierto y no servía: `position()` → `_drain()` → `self._buf = b""` borraba el
   informe que el otro tenía a medio montar en `_frame()`, `_pending` se lo robaba el
   que llegara después, y el resultado era `position() → None` →
   *se perdió la lectura de posición durante el movimiento*. Pasaba en cuanto el
   viaje duraba lo suficiente para que el poll cayera dentro de la ventana de lectura
   de 3 s, y por eso fallaba tanto el viaje largo a la marca 1 como el corto al
   origen: no era lentitud ni el timeout, era una carrera.
   Arreglo: `threading.RLock` en `Panel.__init__` (`Panel._lock`) que envuelve
   `position()`, `handshake()` y —vía `_send()`— los `sendall` de `release`, `hold` y
   `jog_hold`. El cerrojo se suelta en cuanto sale el paquete, nunca durante el pulso,
   así que el poll no espera a que acabe un jog entero. El test de `ruida.py test`
   lanza 4 hilos × 40 lecturas contra un socket que devuelve el informe partido en
   dos trozos con 2 ms de retraso; sin cerrojo se pierden lecturas, con él ninguna.

1. **Viajes nativos (`D9 10`) en `Mover 1/2` y `Origen 0,0`.** `ruida.py` reproduce el
   datagrama capturado de LightBurn, recorta a la mesa (0..500 × 0..400 mm) y confirma
   la llegada por la posición del panel antes de habilitar el siguiente punto
   (`_marca_a` mide el error y avisa en mm si pasa de 0.5; el centrado fina es el
   punto 1.bis). `ir_a` bloquea si hay un
   viaje activo, sin confirmar, o con el jog en mano. Recordatorio de seguridad: el
   viaje **no se cancela** ni desde la app ni con el Stop de LightBurn, y la app avisa
   antes de bloquear el cierre. Lo verificado por captura y confirmación del usuario
   es el destino; el recorrido completo está pendiente de máquina (ver §5).

1.bis. **`Mover 1/2` ahora centra, no solo se acerca.** `hybrid_vision.fine()` existía
   desde la v1.5 pero **la GUI nunca la llamó**: `_marca_a` hacía `goto_native(mm)` y se
   quedaba con que el error fuera < 0.5 mm. Tres cosas que había que arreglar antes de
   conectarla:
   - `fine` tomaba la `VideoCapture` y usaba `grab()`, pero la cámara del cabezal la
     tiene abierta el hilo `Video` del preview; no se puede volver a abrir. Ahora
     `fine(m, foto, cfg, a)` recibe un **callable** que devuelve un frame gris, y la
     GUI le pasa `_foto_cabeza()`, que espera a que el hilo publique un `n` nuevo.
     Siguiente paso si molesta el preview (que no usa el Laplacian de `grab`): parar
     el preview y abrir con `hv.grab`.
   - El centro salía del **centroide de momentos**, que en un aro de grosor desigual
     se va hacia el lado grueso: medido, **6.12 px de error** contra **1.07 px**
     ajustando el círculo (Kasa por mínimos cuadrados, `fit_circulo`). Nuevo
     `forma="circulo"` en `find_marks`; la calibración sigue con centroide porque es
     lo que ya está medido.
   - Corregía con `pan.move_to()`, que son pulsos de 100 ms a 10 mm/s. Ahora corrige
     con `m.goto_native()`, o sea el mismo viaje nativo de `PREGO`.

   Además elige **la candidata más centrada** (no la de mayor área) y pone suelo de
   área a la mitad de la referencia, para que el polvo no llame. Si no converge
   devuelve un motivo y la GUI **no avanza de punto**: deja `PUNTO n: SIN CENTRAR` en
   rojo con el motivo y el aviso de reintentar, y a diferencia de un fallo de
   movimiento no marca `_native_move_fault` (es un problema de luz, no de motor).
   Iteraciones y tolerancia salen de los cuadros de la hoja Marcas y se leen en el
   hilo de la UI (leer widgets Tk desde el worker no es seguro).

   Cobertura nueva en `hybrid_vision.py test`: *ajuste de aro* (elipse de grosor
   desigual + cruz, `ef < 2.0 and ef*3 < ec`) y *centrado* (máquina falsa, foto
   sintética de 720×1280 con el tag de radio 117, offset inicial de 2.24 mm en
   diagonal → 2 viajes por el `max_step` de 2 mm, error final < 0.03 mm; y con la
   marca fuera de plano, `viajes == 0`).
2. **Print and Cut sin sobrepaso en diagonal** (`c81dd8b`): los trayectos largos se
   parten en segmentos XY con temporizador, leyendo posición entre tramos y rematando
   con pulsos cortos; el tiempo por tramo se deriva de la distancia.
3. **Marcas desplazable** (`43ada34`): la hoja va dentro de `Deslizable`
   (Canvas + Scrollbar), la botonera en dos filas, la rueda con `bind_all` saltando
   widgets con scroll propio, y `_cambio_hoja` suelta el continuo + devuelve el foco
   (con WASD pulsado el `KeyRelease` lo recibía la hoja nueva y el cabezal seguía
   andando). Se añadió `self.hoja_marcas` para que la prueba lo encuentre.
4. **Jog compacto y paso con `-`/`+`** (`c458bde`): fuera los tooltips (había que
   esperar 600 ms para leer un icono), el paso se cambia sin soltar el WASD,
   Calibrar enseña los tres visores a la vez, Marcas ya no sale en negro (muestra
   `bed.png` con las manchas re-marcadas) y se arregla el `WinError 10048` que
   abría un segundo panel en el 40207. Las barras de Vivo y Calibrar pasan a `grid`
   de dos filas con `columnconfigure`, que es lo que evita que el último botón se
   salga de la ventana.
5. **Revisión de interfaz, cinco cosas** (esta sesión, commits `16965c7`, `dbaceb5`,
   `1b760d8`, `faa0986`, `7654c3b`):
   - *Estado en color*: `lbl_pos` con `Ok.TLabel`/`Mal.TLabel` (antes un
     `panel: no contesta` salía en negro igual que una lectura buena) y
     `"camaras: abriendo..."` vuelve a `Chico.TLabel` para no quedarse en rojo.
   - *Registro*: `_pie()` pasa a `wrap="word"` dentro de un marco con
     `ttk.Scrollbar` y `fill="both", expand=True` (pedía 2212 px con `fill="x"`).
   - *Textos*: `_wrap_al_ancho()` ata cada `wraplength` al ancho de su marco con
     `<Configure>` y **solo encoge**; las etiquetas sin `wraplength` no se tocan.
   - *Escalado de Windows*: `_dpi_awareness()` en `__init__` **antes de**
     `super().__init__()`; Per-Monitor V2 → `shcore` → `user32`, y el registro
     dice cuál ha quedado. Ojo: el HANDLE necesita `argtypes`, si no ctypes pasa
     un int de 32 y la llamada falla en silencio; y si PyInstaller ya lo puso en
     el arranque no hay arreglo posible.
   - *Pruebas*: `wraplength` a 940 px, el umbral del visor de Marcas (118 px, se
     pedían 130), el reparto de la botonera de Calibrar a 940 px, y el `after` de
     60 ms del corte de jog que dispara `_cambio_hoja` (si no, `ir_a` dice
     "suelta primero el control de jog"). 94 comprobaciones, 0 fallos.

## 0.bis. Lo de la v1.9

1. **La actualización se mira sola al arrancar, sin botón.** El de `Buscar
   actualizaciones` estaba en el pie y no se veía; se quita y en su lugar `__init__`
   encola `self.after(800, self.ota)`, cuando las cámaras ya están abiertas. Sigue
   preguntando antes de instalar (`_ota_vuelve`). La prueba falsea
   `actualizar.comprobar` ANTES de que dispare el `after` (si no, sale a GitHub de
   verdad) y comprueba que el aviso llega al pie, que es lo que demuestra que el
   `after` está bien enganchado.
2. **Calibrar, cuatro celdas iguales.** El `PanedWindow` de la v1.7 se sustituye por
   una rejilla 2×2 (`cuerpo.rowconfigure/columnconfigure` con `weight=1,
   uniform="cal"`): los dos visores en vivo, la foto congelada y la tabla de puntos
   miden lo mismo. Medido: 574×280 / 574×280 / 574×279 / 574×279 (el píxel de menos
   es el reparto entero de la rejilla). La prueba mira `app.celdas_cal` y exige ≤1 px
   de diferencia, no el píxel exacto.
3. **Fuera `Estacionar` de Vivo.** Hacía lo mismo que `Origen 0,0` (park = (20,20),
   origen = (0,0): 20 mm). En Calibrar sigue el `1. Estacionar` de la rutina. La
   prueba exige que `"Estacionar"` no sea el texto de ningún botón suelto.
4. **Print and Cut: las marcas vuelven a verse.** `marcas_utiles` (hybrid_vision.py)
   devuelve píxeles `(u, v)` y `_fin_marcas` los volvía a abrir por `(mm, px)`
   (`[p for _, p in self.marcas_utiles]`): salía una lista de `numpy.float64`,
   `_pintar_marcas` reventaba al abrir el primero y se caían **a la vez** la marca
   verde sobre la foto y el llenado de la lista de coords (`_desdovar` se tragaba el
   error y solo lo dejaba en el registro). Ahora se pasa `self.marcas_utiles` tal cual.
   La prueba es NO VACUA: `len(app.foto_marcas.detectadas) == 2` y
   `find_withtag("marcas")` devuelve 4 (2 óvalos + 2 números); antes solo se miraba
   que la foto existiera, por eso el fallo pasó desapercibido. El botón `Todo de una
   vez` pasa a `Detectar y centrar los 2` y se añade una ayuda que explica el ciclo
   (estaciona → mira la cama → lleva a cada marca → la recentra). `lbl_punto` empieza
   en rojo hasta que hay puntos, y pasa a verde con la posición real del cabezal.

## 0.ter. Lo de la v1.7 y v1.8

1. **El paso es un número en mm, no un desplegable** (`self.paso_mm`, `lbl_paso`,
   `_pon_paso`, `_cambia_paso`): `-` y `+` lo mueven en saltos de 0,1 mm entre
   `PASO_MIN=0.1` y `PASO_MAX=10.0`. El pulsado lo traduce `ms_de_paso()` en
   hybrid_vision.py, que interpola los tres puntos medidos (0,2/0,44/3,4 mm),
   extrapola con la pendiente del último tramo y nunca baja de 1 ms: por debajo la
   Ruida no distingue un pulso de un keyup, así que un paso más fino es
   indistinguible de "no mover". El `jog` de la CLI **no** cambia: sigue con su
   ciclo de tres pasos, es otro uso.
2. **Los botones llevan su nombre encima, no un icono.** Se probó lo contrario (icono
   + `ToolTip` con `place`/`place_forget()`) y fue un error: hay que parar el ratón y
   esperar 600 ms para leer qué hace un botón, y con la app en marcha eso no pasa.
   Fuera `ToolTip`, `_ligar()` y el botón de cámara dibujado a mano. `boton()` es
   ahora solo un `ttk.Button` con el texto y la acción.
   Al poner los nombres, las barras de **Vivo** y **Calibrar** pedían 1500 y 1650 px en
   una ventana de 1200, y los últimos botones quedaban **fuera sin verse**: ahora esas
   barras van en `grid` de dos filas (Vivo: acciones arriba, paso abajo; Calibrar:
   4 botones arriba, 3 abajo y el texto de ayuda en su fila) y miden 875 y 1519 px. La
   prueba mide los 26 botones de las cuatro hojas y falla si alguno se sale de los
   1200 px. Los `−`/`+` del paso y los `+Y/+X/-Y/-X` se quedan: su texto ya es su
   nombre.
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
   `detectar` no mueve el cabezal, deja los dos puntos a la vista; `Mover` va al
   punto 1, se lee la posición en grande y se anota a mano en LightBurn; `Mover` otra
   vez va al punto 2 y avisa de que el offset de LightBurn debe quedar
   desactivado. El mismo botón va cambiando de texto ("Mover 1" → "Mover 2" →
   "Mover"), y se queda en `disabled` mientras no haya puntos.
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
- `boton()` con `width` explícito reventaba por `multiple values for keyword`. Al
  quitar los iconos también se fue el `width` por defecto que lo pisaba: ahora
  `boton(padre, texto, accion=None, **kw)` pasa `**kw` tal cual.
- La leyenda con `withdraw()` reventaba al construir la app (`withdraw` es de `Wm`, y
  un `Label` no es toplevel). Ya no hay leyenda: los nombres van en el botón.
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
4. **Calibrar: cámara y foto.** Al entrar en la pestaña se ve la cámara en directo, y
   con una captura ya congelada el visor ya no se pisa: el botón pasa de **Congelar
   foto** a guardar. `hay_foto` es lo que decide.
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

**La fuente de la verdad de lo que pasa con la máquina es `coplitovs-notas.md`**
(diario de las pruebas físicas). Esto es solo el resumen, en orden:

- **Mover 1 ya lleva el cabezal a la primera marca, pero no queda centrado.**
  Antes de tocar el movimiento, repetir la calibración con puntos bien repartidos
  por toda la cama y revisar error de reproyección, orientación y `cam_offset_mm`.
- **La segunda marca se clasifica fuera del área segura** y no llega a tratarse
  como objetivo. Causa sin aislar (calibración, homografía, ROI, frame o la marca
  detectada): registrar por marca píxeles, mm, ROI y razón del rechazo, y
  compararla con el área segura y con `coords.txt`. **No relajar `Panel.SAFE`**
  para que el punto pase.
- **Comprobar que el visor, las coordenadas mostradas y el destino enviado son
  exactamente la misma pareja seleccionada.**
- **`pick_pair` ya no se queda con el par más separado** (un reflejo de 61 px se
  colaba por una marca): ahora prioriza el área del componente dentro del área
  segura y desempata por distancia. Falta confirmar en la foto, con un fotograma
  nuevo, que las dos retículas caen sobre los discos.
- **Centrar automáticamente el FOV de la cámara del cabezal** sobre el centro de
  la marca circular después de cada llegada. La diferencia observada no es
  constante entre ejecuciones: medir el centro detectado por frame, el offset
  fino aplicado y el error final por iteración antes de cambiar calibración u
  homografía. Offline y sintético primero; movimiento físico solo con
  confirmación explícita del usuario.
- **Probar Mover 1 y Mover 2 por separado**, con el láser deshabilitado y el
  recorrido despejado, cuando lo anterior esté resuelto.
- **Respaldar la calibración** sin sobrescribir el `calib.json` de usuario
  durante las pruebas.

No tocar el offset por las comparaciones: `cmd_run --no-move` entrega los
destinos **sin** `cam_offset_mm` y `save_txt` **sí** lo suma a `coords.txt`, así
que el rótulo verde y el log de detección no son la misma representación de
coordenadas. El usuario confirmó que el tratamiento actual es el correcto.

Lo de la v1.7, en este orden:

- **El flujo de Print and Cut, a pelo**: centrar la plantilla, `◎ Detectar los dos
  puntos`, `Mover` al punto 1, anotar en LightBurn, `Mover` al punto 2, anotar. La posición
  que sale en grande tiene que coincidir con la que marca el cabezal ahí. Luego:
  en LightBurn, offset **desactivado**.
- **`-` y `+` con el cabezal en la mano**: 0,1 mm se nota como un roce, 10 mm se
  nota de golpe, y ni un paso se queda sin mover.
- **El instalador de verdad**: Ajustes → Actualizar, y comprobar que la app se
  cierra, aparece el instalador y arranca. Antes solo se descargaba.
- Los nombres de los botones: que se lean todos en la pantalla de verdad, sobre todo
  "4. Offset laser" y "Copiar coordenadas", que son los que más se Acercan al borde.

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
- ~~El techo de 5 mm/s es del perfil de LightBurn~~ — **era falso.** El viaje nativo
  iba a ~10 mm/s porque faltaban los tres paquetes que LightBurn mete delante de cada
  Go (`c9 02 00 00 00 00 00`, `c6 01 00 00`, `c6 21 00 00`; medidos con `tshark` en el
  50200, des-swizzeando y sin los 2 bytes de checksum). Con ellos sale a >300 mm/s.
  El `D9` en sí ya era byte a byte el de LightBurn, por eso no se veía el problema.
  Ahora son `Ruida.PREGO`. Los 5 mm/s del perfil siguen sin ser un techo real: un
  pulso de 100 ms mide 3,4 mm, no los 0,68 mm que darían 5 mm/s.
- El panel se calla mientras ejecuta el viaje, así que `move_and_wait` ya no aborta a
  la primera lectura perdida (antes tumbaba el viaje entero, que no se puede cancelar).
  Aguanta varias, exige dos lecturas estables y deja el timeout como red de seguridad.

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
/root/venv/bin/python -m ruidavision.prueba_app           # 63 comprobaciones de GUI y jog
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
