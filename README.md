# Sistema de Visión Híbrido para láser CO₂ (Ruida RDC7132G)

Posiciona y alinea marcas de registro (Print and Cut) usando dos cámaras USB IMX179:
**cenital** (coarse, en la tapa) + **cabezal** (microscopio, ajuste fino), hablando con
la controladora por UDP y dejando el terreno preparado para LightBurn.

```
ruida.py          capa de red: mover el cabezal y leer su posición real (sin dependencias)
hybrid_vision.py  cámaras, detección de marcas, homografía y flujo Print and Cut
calib.json        se crea solo en `calibrate` (cámaras, FOV, homografía H, puntos)
```

## Movimiento a las marcas

**Mover 1 / Mover 2 usa el comando nativo de posición observado en LightBurn**,
no el jog manual por teclas. Una captura local `ruida_lan_clean.pcap` registró
`D9 10 00 <X de 5 bytes><Y de 5 bytes>` en UDP 50200. Tres pares decodificados
coincidieron exactamente con destinos introducidos en LightBurn (183.010,
80.000), (199.740, 349.280) y (419.960, 349.278) mm; el usuario confirmó que
el cabezal llegó a ellos. `ruida.py` reproduce ese datagrama, restringe las
coordenadas a 0..500 × 0..400 mm y verifica la llegada con la posición del panel
50207. Los opcodes `0x88`/`0x89` sueltos y las teclas simultáneas no son
equivalentes a este comando.

El viaje nativo **no se puede cancelar desde la aplicación ni desde el botón
Stop de LightBurn**; el controlador termina el desplazamiento solicitado.
Print and Cut avanza un punto por pulsación para que el operador pueda revisar
cada llegada. Durante un viaje, la app advierte que **Parar no lo interrumpe**
y bloquea el cierre; deja libre la trayectoria y usa el paro físico de la
controladora ante una emergencia. Cierra LightBurn antes de mover: ambos
programas usan el puerto local 40200.

`Origen 0,0` usa el mismo viaje nativo con destino fijo (0, 0), comprueba la
llegada y bloquea nuevos viajes si queda sin confirmar. No es cancelable desde
la app. `Estacionar` en Calibrar y **Detectar y centrar los 2** siguen
deshabilitados; el jog manual permanece en 50207. Antes de probar, verifica que
el láser esté deshabilitado y deja despejada la trayectoria.

El comando `D9 10` no incluye una velocidad: el perfil/estado de la controladora
determina el ritmo. La app no puede acelerarlo con un parámetro de viaje. Si la
velocidad parece anormal, comprueba el estado de la máquina con LightBurn antes
de continuar; un timeout de confirmación no significa que el movimiento se haya
detenido.

En **Calibrar** hay un pad compacto de jog direccional (toque = paso; mantener =
continuo), ajuste del paso con rueda en saltos de 0.5 mm, acceso para conectar
cámaras y visores en vivo cenital/cabezal. Tras congelar una foto cenital se
pueden variar el umbral (`0` usa Otsu) y el área mínima y aplicar de nuevo el
detector a esa misma foto. **Print and Cut** también ofrece el jog y un visor en
vivo ampliado del cabezal (panel lateral de 360 px, con retícula central
resaltada) junto a la foto de detección. Los controles del detector afectan
solo a la ayuda visual de Calibrar: no cambian Print and Cut ni guardan ajustes
globales. La captura reconecta las cámaras que estaban activas incluso si falla.

En la última prueba física, **Mover 1 alcanzó la primera marca**, pero no quedó
centrado como se esperaba; hay que repetir la calibración y revisar la homografía
y el offset antes de atribuirlo al movimiento. El detector también clasificó la
segunda marca fuera del área segura. Investigar la coordenada, ROI y calibración
sin relajar los límites de viaje.

La detección resalta las mismas coordenadas/píxeles seleccionados por el
detector con retículas grandes y numeradas; el registro indica cuántas se
dibujaron y sus píxeles. Al probar desde el código, abre
`py -3 -m ruidavision.app` desde la raíz del repositorio y revisa
`%LOCALAPPDATA%\Ruida Vision\app.log`.

## Qué cambió en la v1.9

Cuatro cosas que salieron de usar la v1.8 con la máquina delante:

- **La actualización se mira sola al arrancar.** El botón `Buscar actualizaciones`
  desapareció: estaba en el pie, no se veía y nadie lo pulsaba, así que las
  versiones nuevas solo llegaban a quien se acordaba. Ahora la app consulta GitHub
  a los 0,8 s de abrirse (cuando las cámaras ya están en marcha) y avisa en el pie;
  instalar sigue siendo cosa tuya, con el mismo aviso de siempre.
- **Calibrar reparte la hoja en cuatro celdas iguales.** Los dos visores en vivo,
  la foto congelada y la tabla de puntos van en una rejilla 2×2 con filas y
  columnas del mismo peso: los cuatro miden exactamente lo mismo (574 px de ancho
  en ventana de 1200). Antes era un `PanedWindow` con la izquierda al 75%, la foto
  encima de las cámaras y la tabla en una franja.
- **Fuera el botón `Estacionar` de Vivo.** Hacía lo mismo que `Origen 0,0`: la
  posición de estacionamiento es (20, 20) y el origen (0, 0), 20 mm de diferencia
  que no cambian nada. Queda `Origen 0,0`. En Calibrar sigue el `1. Estacionar` de
  la rutina de calibración, que sí es un paso con nombre propio.
- **Print and Cut vuelve a enseñar las marcas.** Los dos puntos detectados se
  dibujaban en verde sobre la foto y la lista de coordenadas se llenaba… hasta que
  un doble desempaquetado lo reventaba en silencio: `marcas_utiles` ya devuelve
  píxeles y `_fin_marcas` los volvía a abrir por `(mm, px)`. Salía una lista de
  números sueltos, `_pintar_marcas` fallaba al abrir el primero y se caían **la
  marca verde y la lista a la vez**, con un error solo en el registro. El botón
  `Todo de una vez` pasa a llamarse `Detectar y centrar los 2` para que se lea lo
  que hace: estaciona, mira la cama, lleva el cabezal a cada marca y la recentra
  (el estacionamiento es lo que parece "recorrer las esquinas"; las marcas, no).

## Qué cambió en la v1.8

- **Los botones vuelven a llevar su nombre.** En la v1.7 se les puso icono con leyenda
  flotante y fue mala idea: para saber qué hacía un botón había que parar el ratón y
  esperar medio segundo, y en mitad de un trabajo eso no pasa. Los nombres van en dos
  filas donde no caben en una, para que ninguno quede fuera de la ventana.

## Qué cambió en la v1.7

Siete ajustes que salieron de usar la v1.6 con la máquina delante:

- **El paso se pide en milímetros, no en nombres.** `-` y `+` lo suben y lo bajan
  en saltos de **0,1 mm** entre 0,1 y 10, y el número se ve siempre junto al
  botón, como en LightBurn. Desapareció el desplegable: pulsabas `-` con la mano
  en el teclado y tenías que soltar WASD para tocarlo. La Ruida, en cambio, solo
  entiende pulsos: `ms_de_paso()` traduce mm a

  milisegundos de pulsado con los tres puntos medidos (0,2 / 0,44 / 3,4 mm) y un
  mínimo de 1 ms, por debajo del cual la controladora no distingue el pulso.
- **Los botones llevan su nombre encima**, sin iconos: se probó con icono y leyenda
  flotante y había que parar el ratón y esperar medio segundo para saber qué hacía
  cada uno. Los nombres se reparten en dos filas donde no caben en una.
- **Calibrar reparte la hoja**: las dos cámaras en vivo ocupan la parte de arriba
  y la foto congelada, la de abajo, en una fila a todo lo ancho. Antes la
  congelada se comía la hoja y las cámaras en vivo salían en una franja.
- **Solo se marcan los puntos que están en la cama.** Las manchas que caen fuera
  del área de trabajo (500 × 400 mm) son tags de calibración o reflejos: se
  cuentan y se dicen, pero no se marcan, y **el filtro es el mismo en la app y en
  `hybrid_vision.py`** (`marcas_utiles`). Es lo que evita que el par elegido sea un
  tag y una mancha del borde en vez de las dos marcas del material.
- **Print and Cut sin copiar y pegar.** Con los dos puntos a la vista, el botón
  `Mover` va al primero; se anota a mano la posición que da el cabezal; vuelve a
  pulsar y va al segundo. El paso 2 avisa de que el offset de LightBurn debe
  quedar **desactivado**, porque aquí ya se ha tenido en cuenta.
- **La actualización se ejecuta de verdad.** Descargar el `.exe` ya no se quedaba
  en el disco: la app se cierra (`/CLOSEAPPLICATIONS`), espera a salir y arranca el
  instalador (`actualizar.lanzar`, con `DETACHED_PROCESS` para que no se quede
  colgada esperando).

## Qué cambió en la v1.6

Lo que se arregló en esta versión, todo a raíz de la usar en la máquina:

- **Las teclas `-` y `+` cambian el paso del toque** sin soltar el WASD. El paso
  fino se usa justo mientras se está moviendo, y hasta ahora había que soltar la
  tecla para ir al desplegable. Sigue estando el desplegable, que va a la par.
- **Calibrar enseña los tres visores a la vez**: las dos cámaras en vivo (con la
  cruz del cabezal y la caja de viaje) y, debajo, la foto congelada con las manchas
  en verde. Antes la cámara en vivo y la congelada compartían un mismo lienzo, así
  que solo se veía una de las dos.
- **Marcas (Print and Cut) ya no sale en negro.** Al terminar el run se ve lo que
  vio el detector — la foto de la cama con las manchas marcadas — al lado de la
  lista de coordenadas, y arriba hay dos líneas de cómo se usa la pestaña. Se puede
  comprobar si ha detectado 2 manchas o 20 antes de tocar nada.
- **Ajustar los puntos ya no dice "Error de reproyeccion"** cuando el ajuste es
  bueno. Ahora dice "residuo del ajuste", y recuerda que 0,1–0,2 mm es lo normal.
  (Lo del residuo va en el registro y en el mensaje de la propia pestaña.)
- **Arreglado el `WinError 10048` que rompía la pestaña Marcas.** El panel de la
  Ruida escucha en un puerto fijo (40207) y el run de marcas abría un segundo en el
  mismo puerto: `bind()` fallaba, el run se caía sin escribir `coords.txt` y la
  pestaña se quedaba en "sin coordenadas: mira el registro de abajo". Ahora la app
  suelta su panel antes de arrancar el cálculo, y lo vuelve a abrir después.

## Qué cambió en la v1.5

Lo que se arregló en esta versión, con lo medido en esta máquina:

- **El OTA no descargaba nada.** `actualizar.py` pedía la release y luego escribía el
  fichero con una ruta relativa al directorio del ejecutable, que en Windows bajo
  `Program Files` no existe: `PermissionError` y descarga a medias. Ahora descarga a
  `%TEMP%` con ruta absoluta, y **solo** cierra la app si la descarga se ha podido
  abrir. Sin cambios en la API ni en el repo (siguen sin token, y el repo es público
  a propósito: contra un repo privado la API devuelve 404 aunque exista la release).
- **Las cams tardaban 8 s en abrir y iban a 2 fps.** No era hardware: `cap.set(FOURCC)`
  después de abrir deja el driver en YUY2. Pedir MJPG + resolución + fps en el
  constructor: 0,9 s y 24,9 fps. Ver "Las cámaras: el MJPG hay que pedirlo al abrir".
- **Origen y destinos iban a tirones.** En la v1.5 `move_to` era todo pulsos de 100 ms
  (3,4 mm) con una lectura de posición en medio: una rampa de aceleración por cada
  3,4 mm. Esa versión pasó a jog continuo por eje (~5 mm/s), pero el corte basado
  en lecturas podía llegar tarde. Después se probaron teclas simultáneas, pero los
  desplazamientos físicos no fueron interpolados ni fiables. Una captura de LightBurn
  reveló el comando nativo a coordenadas que usa ahora Print and Cut; ver
  "Movimiento a las marcas".
- **En Calibrar, la cámara en directo y la foto.** Al entrar en la pestaña se ve la
  cámara en directo en el visor, y el botón se llama **Congelar foto**. Con una captura
  ya congelada, el visor ya no se pisa: el botón pasa a guardar.
- **Numerar las manchas de calibración.** La columna "n" de la tabla de Calibrar es
  el número de verdad, no el orden de la lista: se puede renumerar a mano
  ("Cambiar n°"), "Renumerar" ordena por número y renumera 1..N, y `cal_ajusta`
  empareja cada número con su punto **por ese número**, no por posición. Apuntar 70
  manchas de un tirón ya no obliga a dejarlas en orden de captura.

## Dónde corre

En **el PC Windows al que están enchufadas las dos IMX179 por USB** (cenital en la
tapa, cabezal/microscopio). No hay Raspberry en este proyecto. La controladora
Ruida se alcanza por la LAN del PC, así que ese PC tiene que estar en la misma
red que ella.

`opencv-python` en Windows usa DirectShow (`CAP_DSHOW`) para las cámaras USB, con
recurso a `CAP_ANY`. Los índices de cámara se numeran por orden de conexión, así que
**cualquier otro dispositivo USB que se enumere se cuela en la numeración**. En esta
máquina hay tres: las dos IMX179 y la cámara del Galaxy A56 que expone **Enlace de
Windows / Phone Link** como dispositivo virtual.

Índices detectados (verificar con `scan` si se cambia algo):

| índice | cámara | config |
|---|---|---|
| 0 | IMX179 del cabezal (microscopio) | `head_cam` |
| 1 | Galaxy A56 vía Enlace de Windows — **descartada** | — |
| 2 | IMX179 cenital (tapa) | `top_cam` |

```bash
py hybrid_vision.py scan      # abre cada indice, dice la resolucion real y
                              # guarda scan_idx<N>.png para identificarlo a ojo
```

Si desconectas el Galaxy, los índices se recomponen: vuelve a pasar `scan` y actualiza
`top_cam` / `head_cam` en `calib.json`. Para probar sin editar nada:
`py hybrid_vision.py cams --top 2 --head 0`.

## Instalación

```bash
pip install -r requirements.txt
python hybrid_vision.py test        # autocomprobado, no toca la máquina
```

`test` valida el protocolo (vectores dorados, swizzle, informe de posición) más la
homografía y la detección sintéticas. Si pasa, la parte que no depende del vídeo está bien.

Para abrir la app desde el código en Windows:

```powershell
py -3 -m ruidavision.app
```

La app instalada y la lanzada desde el código reutilizan
`%LOCALAPPDATA%\Ruida Vision\calib.json` cuando existe. Así se conserva la calibración
de la instalación al ejecutar la versión del repositorio. Si no hay calibración de
usuario, la versión de código usa `calib.json` junto a `hybrid_vision.py`. La ruta
activa aparece en el registro al abrir la ventana.
El registro de la app se guarda en `%LOCALAPPDATA%\Ruida Vision\app.log` cuando hay
datos de usuario activos; el botón **Ver registro** abre ese archivo.

## Paso 0 — el número mágico (hazlo una vez, antes de mover nada)

La controladora fuezia con un byte "magic" al principio de cada comando de movimiento.
El valor correcto no está documentado para la 7132G, así que hay que deducirlo:

1. En LightBurn o RDWorks crea un trabajo mínimo (un cuadrado pequeño) y guárdalo
   como `.rd`.
2. Deduce el magic:

```bash
py ruida.py magic C:\ruta\al\archivo.rd
```

**Resultado en esta máquina: `0x88`**, que es el valor por defecto, así que no hace
falta pasar `--magic` nunca. Verificado sobre un `Default.rd` de RDWorks de 21503
bytes: 0x88 des-swizzleado da un 28.0% de bytes `0x00` (padding), y el siguiente magic
se queda en 8.8%. Los dos viajes absolutos del fichero decodifican a 328.007 ×
133.240 mm, coordenadas de cama plausibles.

Cómo lo decide (por si hay que repetirlo en otra controladora): en un `.rd` real el byte
más frecuente es `0x00`, y solo el magic correcto lo devuelve a cero. **No se cuentan
opcodes a propósito**, porque `swz(0x00, 0x88) == 0x89`: el padding se serializa justo
como el opcode de viaje relativo, y hay 7 magics que lo mapean a un opcode, así que
cualquier recuento de opcodes se autoengaña. Ese error costó tres versiones de la
heurística antes de cazarlo con un `.rd` real.

Anótalo igualmente y úsalo siempre: `--magic 0xNN` (o en el `calib.json`).

Si **ninguno** destaca, para y no muevas el cabezal: significaría que el `.rd` no
tiene viajes o que no es de RDWorks para un 7132G. Prueba otro `.rd`.

## Paso 1 — el origen de la Ruida

Las coordenadas absolutas del cable son **enteros sin signo de 5 bytes** (µm). No
admiten negativos, así que:

- Fija el origen en la **esquina inferior izquierda de la cama**.
- **No lo cambies** entre la calibración y el uso, ni muevas la máquina con los
  pulsadores después de calibrar. Si el origen cambia, las coordenadas quedan
  desplazadas y el corte sale desplazado.
- La homografía calibrada incluye ese desfase; si lo tocas, recalibra.

## Paso 2 — comprobar la red

```bash
ping -c3 <IP>          # red y ruta
python ruida.py ping   # handshake 50207 + 5 posiciones reales
```

`ping` correcto = ves coordenadas que cuadran con lo que pone LightBurn. Si no
responde nada: firewall, o la tarjeta en otra IP/subred (esta subred es fija por DHCP).

**El canal 50207 no manda informes por su cuenta.** Solo responde a un `0xCC`, y su
contestación viene con el informe `A5 68` pegado. Por eso `position()` pregunta cada
vez antes de esperar, y `handshake()` guarda el informe en vez de tirarlo. Sin las dos
cosas el canal parece mudo: es un fallo de lectura de posición, no de red.

Medición real en esta máquina (7132G, magic `0x88`): en reposo el cabezal reporta
`(18.576, 0.0) mm`. Ten en cuenta que son coordenadas de la **máquina**, no de la
cámara.

## Paso 3 —ajuistar el movimiento (con el láser apagado y la cama vacía)

```bash
python ruida.py move 100 100   # pide confirmación interactiva
python ruida.py jog 2 -1
python ruida.py sniff          # vuelca el tráfico 50207 en crudo
```

Si `move` mueve a coordenadas que no cuadran, casi siempre es el magic. Vuelve al
paso 0.

## Paso 4 — calibrar la homografía

```bash
python hybrid_vision.py cams        # ceital | cabezal, q para salir
python hybrid_vision.py calibrate --points 6
```

El script pide, N veces:

1. Mueve el cabezal a un punto de referencia **con W A S D** (o a mano con el mando
   de LightBurn).
2. Enter → el script lee la (X, Y) real de la Ruida.
3. El cabezal se va al **estacionamiento** para despejar la vista de la cámara cenital.
4. Clic en el píxel de la marca en la foto.

Con 4+ puntos bien repartidos (las 4 esquinas de la cama y alguna intermedia) calcula
`cv2.findHomography` con RANSAC y guarda el error de reproyección. **Si el error máximo
sale de 1 mm, no sigas**: mide los puntos con más precisión, quita reflejos o repite.
Un 0.1 mm de error en la homografía es un 0.1 mm de error en cada marca.

**El error que se informa es solo sobre los puntos que RANSAC acepta.** RANSAC
descarta los que no cuadran —típicamente un clic en la marca de al lado— y el
informe los cuenta aparte, uno a uno, en vez de contaminar la media. Los puntos
descartados **no se guardan**, para que la siguiente calibración no los vuelva a
meter. Medir también sobre los descartados es lo que hacía que una calibración
buena (5 puntos a 0.8 mm) se anunciara como un desastre de 477 mm.

Sin informe de posición de la Ruida: `--manual` para escribir X Y a mano.

## Paso 5 — flujo Print and Cut

```bash
python hybrid_vision.py run --marks 2 --debug --emit coords.txt
```

1. Cabezal al estacionamiento, foto general de la cama.
2. Detecta las manchas, descarta las que proyectan fuera de la cama y elige la
   pareja con los **componentes de mayor área**; la distancia desempata. Así un
   reflejo pequeño dentro de la zona segura no reemplaza una marca real solo por
   estar más lejos.
3. Por cada marca: mueve el cabezal allí y entra en el lazo de centrado fino.
4. Imprime las coordenadas finales y las deja en `coords.txt`.

### La caja de la máquina es la ROI por defecto

`run` descarta toda marca que la homografía proyecte **fuera de la caja de la
máquina** (`Panel.SAFE`, 0-500 x 0-400 mm), e imprime cuáles eran. No es
cosmético: una homografía ajustada con puntos en una zona **extrapola** mal
fuera de ella, y un tag de calibración cerca del borde sale en milímetros que no
existen. Moverse a uno de esos es un `ValueError`, y si el soft limit fuera mayor
sería el cabezal contra un tope de recorrido.

Si al filtrar quedan menos de las dos marcas pedidas, el motivo suele ser tags de
calibración de por medio: se acota con `--roi x0,y0,x1,y1` en mm. Y si lo que
sobra está **fuera** de la caja, la homografía extrapola mal ahí: repite
`calibrate` con puntos repartidos por **toda** la cama, esquinas incluidas. Una
homografía solo es fiable dentro de la región donde se midió.

El bucle de ajuste fino mide el desplazamiento marca↔centro de imagen en la cámara
del cabezal, lo convierte a mm con `head_fov_mm` y mueve el cabezal en sentido
contrario hasta el punto absoluto calculado, con `move_to` (lazo cerrado, medido en
cada paso). Repite hasta estar a `--tol` o `--iters` iteraciones.

Ojo al `--tol`: el paso mínimo del teclado de jog es ~0.2 mm, así que **0.1 mm es
la tolerancia que converge de forma fiable** (medido: los 4 objetivos de prueba
entre 0.075 y 0.144 mm, en 6-15 s). Con `--tol 0.05` el lazo no resuelve el último
0.2 mm, se queda paseando y agota el presupuesto: 2 de 4 lo alcanzan y los otros 2
tardan 41 s. Para registro de print-and-cut, 0.1 mm sobra.

**Ojo con el signo de la cámara del cabezal.** Puede ir girada o espejada respecto a
los ejes de la máquina, y no hay forma de saberlo hasta mirarlo: si está mal, cada
iteración aleja la marca en vez de acercarla. Se comprueba en la primera pasada
(fase 1, láser apagado, `--iters 1`): mira la línea que dice `offset`. Si al hacer
jog la marca se aleja, pon `head_flip_x: -1` (y/o `head_flip_y: -1`) en `calib.json`.
Con el ajuste correcto el offset debe decrecer en cada línea de log.

Escribe las coordenadas en LightBurn con **Obtener coordenadas** en cada punto de
registro, y dale a cortar desde ahí.

### Cómo se centra el cabezal en cada punto (y por qué no hace falta puntero)

`calibrate` guarda el par *(posición del cabezal) ↔ (píxel donde haces clic)*, así
que asume que el cabezal está **centrado en la marca** al pulsar Enter. Da igual
que no haya un puntero superpuesto: la marca centrada en la imagen de la cámara del
cabezal **es** el puntero, y con 0.0255 mm/px el centrado se juzga a ~0.1 mm con el
ojo. `calibrate` abre las dos cámaras, así que la del cabezal se usa para eso:

```
py hybrid_vision.py calibrate --park 20,20
```

En cada punto, `calibrate` abre **una ventana con las dos cámaras a la vez**:

- **Izquierda, la del cabezal**, con la cruz y el recuadro verde de la ventana de
  búsqueda dibujados: la marca tiene que quedar centrada en la cruz.
- **Derecha, la cenital**, para ver el contexto y elegir el siguiente punto, con un
  círculo rojo en donde está el cabezal ahora (si ya hay homografía guardada).

**No hace falta LightBurn**: el cabezal se mueve con **W A S D** (arriba, izquierda,
abajo, derecha), un paso por pulsación, y `v` cambia el tamaño del paso (0.2 / 0.44 /
3.4 mm, pulsos deadbeat de 1, 20 y 100 ms). Para trayectos largos, `v` hasta el paso
de 3.4 mm; para el último milímetro, `v` otra vez hasta 0.2 mm. Abajo es `S` porque el
origen está en la esquina inferior izquierda.

Se usan letras y no las flechas a propósito. Las flechas del teclado llegan partidas
en dos golpes y el segundo byte no aparece en esta máquina, así que el código las
recibe como un prefijo suelto y no puede saber hacia dónde apuntabas. Una letra llega
siempre, en un solo golpe, y da igual el teclado y el idioma. Las flechas siguen
mapeadas por si en otro equipo llegan enteras, pero **la ruta buena es WASD**.

Da igual dónde esté el foco del teclado. La ventana de OpenCV solo ve las teclas si el
foco está en ella, y la consola solo las ve si el foco está en la consola; el código
lee las dos fuentes en cada vuelta y se queda con la primera que conteste, así que
funciona con la ventana delante y con la consola delante.

Si alguna vez sale en pantalla `tecla 0x... (…) sin asignar`, ese es el código crudo
de una tecla que no está en la tabla: el número es justo lo que hay que mirar para
añadirla.

Pulsas **Enter** cuando la marca está centrada, y entonces el código aparta el
cabezal con `park()`, saca el frame de la cenital y te pide el clic en la marca. Ese
clic va a 0.26 mm/px, o sea que **1 px de error = 0.26 mm**: haz clic en el centro de
la mancha, no en el borde. `q` sale en cualquier momento.

Dos ventanas por un motivo: la compuesta es para **navegar** (saber dónde está el
cabezal y qué marca elegir de las muchas que hay en la cama) y el frame a pantalla
completa que sale al pedir el clic es para **apuntar con precisión**, que a media
resolución no sirve. Con `--no-watch` se hace por consola, escribiendo el X Y a mano.

## Jog manual y viaje nativo a coordenadas

**El jog manual usa las teclas del panel 50207.** Los paquetes `0x88` y `0x89`
sueltos por el 50200 se confirman con un `c6` pero no movieron esta controladora
en las pruebas previas.

Para un viaje absoluto, la captura de la acción LightBurn **Move > Move to
Position > Go** muestra `D9 10 00 <X:5 bytes><Y:5 bytes>` por UDP 50200. Tres
destinos decodificaron exactamente y se confirmó que el cabezal los alcanzó.
Print and Cut reproduce ese datagrama, recibe el ACK y consulta la posición en
50207 hasta confirmar llegada. No se han de sustituir esos bytes por `0x88`,
`0x89` ni por jogs simultáneos.

El 50207 sí mueve, con teclas de jog. Tabla medida en esta máquina, que es la
opuesta a la documentación:

| byte | acción | letra de verificación |
|------|--------|----------------------|
| `01` | +X | `jog 2 0` aumenta X |
| `02` | −X | `jog -2 0` disminuye X |
| `03` | −Y | `jog 0 -2` disminuye Y |
| `04` | +Y | `jog 0 2` aumenta Y |

Se manda `A5 50 <tecla>` para pulsar y `A5 51 <tecla>` para soltar.
La tabla se verificó eje por eje. Una prueba posterior confirmó que mantener
dos teclas a la vez no da una trayectoria interpolada fiable; no combinar
teclas para movimiento automático.

### En la GUI: un toque o mantener

En la app (`ruidavision/`) un **toque corto** de la tecla o del botón da un solo paso
fino (el del cuadro "paso"), y **mantener** pulsado mueve en continuo con `jog_hold`.
Print and Cut usa el viaje nativo `D9 10`, no pulsaciones temporizadas. Cada tarea de
máquina corre en su propio hilo, de modo que mantener una tecla **no congela la ventana**.

El botón **Parar** suelta las cuatro teclas del panel y detiene el jog manual. No
interrumpe el viaje nativo a coordenadas; ante una emergencia usa el paro físico.

El techo de velocidad del continuo es el del perfil de LightBurn (5 mm/s), no algo que
se ajuste desde la app: `set_param` por red no cambia nada (ver la sección de abajo).

### Las cámaras: el MJPG hay que pedirlo al abrir

Poner el fourcc con `cap.set()` **después** de abrir no lo negocia: el driver se queda
en YUY2 y, a 1080p, son 3,7 MB por fotograma. Medido en esta máquina con la cenital:

| cómo se abre | fourcc real | primer fotograma | fps |
|---|---|---|---|
| `cap.set(FOURCC)` después | YUY2 | 8,4 s | 2,2 |
| MJPG + resolución + fps en el constructor | MJPG | 0,03 s (0,9 s hasta abrir) | 24,9 |

Por eso `open_cam` pasa los tres parámetros a `cv2.VideoCapture(...)` y deja el `set`
para la exposición y el gain, que sí aceptan el cambio en caliente. Si el constructor
con parámetros no abre, se cae al camino de antes (abrir y luego negociar), y si el
frame real no es el pedido lo avisa y usa el real: la homografía se escala por
`frame/cal` (`_px_de_mm`), así que acertar la resolución no es crítico, pero el
rendimiento sí.

### La resolución: qué la baja y qué no

Un pulso de `t` ms recorre `~0.18 mm (desplazamiento fijo) + velocidad · t`. Con el
perfil **"Config maquina jog lento"** (`0x27`/`0x37` = 5 mm/s, `0x28`/`0x38` =
800 mm/s², subido a la Ruida con LightBurn) queda así:

| pulso | X | Y | (X con el perfil de fábrica) |
|-------|---|---|------------------------------|
| 1 ms | 0.200 mm | 0.175 mm | 0.219 mm |
| 2 ms | 0.213 mm | 0.187 mm | 0.251 mm |
| 10 ms | 0.302 mm | 0.276 mm | 0.498 mm |
| 20 ms | 0.441 mm | 0.410 mm | 0.854 mm |
| 50 ms | 1.045 mm | 1.019 mm | 4.56 mm |
| 100 ms | 3.403 mm | 3.353 mm | 6.15 mm |

Dos cosas que se aprendieron midiendo, y que no salen de leer la documentación:

1. **Bajar la velocidad no baja la resolución.** El desplazamiento fijo de ~0.18 mm
   es una distancia, no una latencia: se paga una vez por pulso y siguio igual al
   bajar la velocidad 3× (0.219 → 0.200 mm en el pulso de 1 ms). El paso mínimo
   sigue siendo ~0.2 mm. Lo que sí mejora es la seguridad y la velocidad de
   aproximación: un pulso de 50 ms pasó de 4.56 mm a 1.045 mm.
2. **El perfil lento además arregla la asimetría entre ejes.** Con el de fábrica X
   iba a 0.092 mm/ms y Y a 0.045; ahora los dos van a 0.031, así que `JOG_RATE` es
   un número y no un diccionario por eje.

Como el paso mínimo no baja de 0.2 mm, el `--tol` fiable es 0.1 mm, no 0.05.

### Dónde se cambia la velocidad de jog

En el perfil de máquina de LightBurn, **no en el panel**: el `.lbset` es JSON y trae
los parámetros **por eje** (`0x2X` = X, `0x3X` = Y). Los del teclado son:

| id | parámetro | valor |
|----|-----------|-------|
| `0x27` | Keypad jumpoff speed X (mm/s) | 5 |
| `0x28` | Keypad acceleration X (mm/s²) | 800 |
| `0x37` | Keypad jumpoff speed Y (mm/s) | 5 |
| `0x38` | Keypad acceleration Y (mm/s²) | 800 |

Solo se tocan los del teclado (`Keypad ...`): los de corte (`0x23` Max speed, `0x24`
Jumpoff speed, `0x25` Max acceleration) no se tocan, que son los que gobiernan el
movimiento durante el grabado.

El archivo de fábrica **no describe la máquina**: dice 15 mm/s donde se midieron
92 mm/s. Sirve como referencia, no como estado real.

**No se puede cambiar por cable.** `Ruida.set_param()` (paquete `e7`, el mismo que
abre un `.rd`) lo confirma la controladora con un `c6` y no cambia nada: medido a
0.225 mm por pulso de 1 ms con el parámetro a 15, a 5 y a 2. Es el mismo muro que el
movimiento: el 50200 acusa recibo y no ejecuta nada fuera de un trabajo en curso.
Hay que subir el perfil con LightBurn conectado a la máquina.

`Panel.release()` suelta las cuatro teclas al abrir sesión: un proceso muerto a
mitad de un pulso deja el teclado del panel bloqueado y después ignora las
pulsaciones nuevas.

### Topes de seguridad

`Panel.SAFE` conserva la caja medida (0..500 × 0..400 mm). Los destinos nativos
se validan en esa caja antes de enviar `D9 10`; el informe 50207 confirma la
llegada. `Panel.move_to` sigue rechazando destinos por jog temporalizado.

## Coexistencia con LightBurn / RDWorks

El 50200 usa el puerto origen 40200, que es el mismo que ocupa RDWorks; el 50207 usa
el 40207. Si tienes las dos cosas abiertas:

```bash
python ruida.py ping --src 40200
```

Si el bind falla, hay conflicto de puertos. Además, **no muevas el cabezal a mano
desde LightBurn mientras corre `run`**: los dos escriben en el mismo canal y las
coordenadas se mezclan. Cierra el trabajo en LightBurn, ejecuta `run` con las
cameras ya fijas, y vuelve a abrir LightBurn con las coordenadas.

## Seguridad

- Este código **nunca manda órdenes de corte ni de movimiento del cabezal Z**
  (no toca `0xA8`/`0xA9` ni el bobinado). Mueve la cabeza, no el láser.
- Ajustes de potencia, aire, o corte se hacen en LightBurn, no aquí.
- Prueba siempre con el láser **deshabilitado** y la propia pieza ya en la cámara.
- `run --no-move` detecta y calcula coordenadas sin mover nada: úsalo para validar
  la homografía antes de la primera pasada con la máquina en marcha.

## Supuestos y límites conocidos

- **La 7132G no está en la lista de magics documentada.** Por eso el paso 0. El valor
  por defecto (0x88) es el de la 644XG/654XG y es una apuesta, no un dato.
- El checksum va como jnweiger (`sum` de los bytes ya swizzleados); el wiki dice
  "pre-swizzle". Si los movimientos fallan pero el ACK llega raro, es esto.
- Los ACK/RSP varían por firmware: se aceptan `0xC6` y `0xCC` como ACK, `0x46` y
  `0xCF` como error.
- ~~El informe `A5 68` se asume que es la posición del cabezal.~~ **Verificado en esta
  máquina**: el 50207 reporta `(18.576, 0.0)` en reposo y LightBurn muestra exactamente
  lo mismo. Es la única lectura de posición que existe y ya se puede usar para
  confirmar movimientos.
- `find_marks` es "componentes oscuros de área plausible". Detecta puntos y cruces
  pero **no distingue un punto de una letra o de un trozo de borde oscuro**. Si ves
  detecciones falsas, ajusta `--thr`/`min_area` en `calib.json` o añade un filtro de
  redondez: `cv2.HoughCircles` o la relación de aspecto de la componente.
- La escala de la cámara del cabezal (`head_fov_mm`) es una constante, no una
  calibración. Es la fuente de error nº 2 después de la homografía: mídela con
  `--manual` contra una rejilla conocida.
- **El driver puede no darte la resolución que pides.** La cenital ha dado
  1920x1080 pero la del cabezal 1280x720 con el mismo `--width/--height`. El
  código usa siempre la resolución real del frame (la imprime al abrir y avisa si
  no coincide), así que los mm por píxel del ajuste fino son correctos. Pero
  `head_fov_mm` se mide sobre la resolución que entrega de verdad: a 1280 de
  ancho son 0.023 mm/píxel con un FOV de 30 mm, y por debajo de ~0.02 mm/píxel
  el centrado no mejora de tolerancia.
- Sin `find_marks` por plantilla: si las marcas son "círculos o cruces" muy concretos,
  la detección por momentos puede fallar, y la forma de la componente es el sitio
  donde añadir la comprobación.
- No hay reading de homografía por `DA 00 XX XX`; implementado solo el canal 50207.
- Multiusuario: `run` asume que nadie más toca la máquina.
