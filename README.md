# Ruida Vision — visión híbrida para láser CO₂ (Ruida RDC7132G)

Posiciona y alinea **marcas de registro (Print and Cut)** con dos cámaras USB
IMX179: una **cenital** en la tapa (visión general de la cama) y otra en el
**cabezal** (microscopio, ajuste fino). Habla con la controladora por UDP y deja
las coordenadas listas para pegarlas en LightBurn.

El PC que ejecuta la app es el mismo al que están enchufadas las cámaras y debe
estar en la red de la Ruida. No hay Raspberry en este proyecto.

> **Aviso:** software experimental, distribuido **sin ninguna garantía**. Mueve
> la cabeza de un láser CO₂: revísalo antes de usarlo, pruébalo con el láser
> deshabilitado y úsalo bajo tu propia responsabilidad. No sustituye a los paros
> de seguridad de la máquina.

```
ruida.py          red: mover el cabezal y leer su posición real (sin dependencias)
hybrid_vision.py  cámaras, detección de marcas, homografía y flujo Print and Cut
ruidavision/      la app de escritorio (Tkinter); se arranca con ruidavision.app
calib.json        cámaras, FOV, homografía H y puntos de calibración
```

---

## Instalación y arranque

### Instalador para Windows

Descarga el instalador de la última versión desde
[GitHub Releases](https://github.com/JesPezz/ruida-vision/releases) y ejecútalo.
Instala en `C:\Program Files\Ruida Vision`, crea los accesos del menú Inicio y,
si lo marcas, el del escritorio.

- **Datos de usuario:** `%LOCALAPPDATA%\Ruida Vision\`
  - `calib.json`: cámaras, FOV, homografía y puntos.
  - `app.log`: registro de la app.
  - `coords.txt`: coordenadas de la última tarea de Print and Cut.
  - `bed.png` y `cabeza.png`: últimas fotos usadas por el flujo.
- El instalador **no pisa** un `calib.json` que ya exista: actualizar la app
  conserva la calibración. Desinstalar borra el log, pero no la calibración.

### Desde el código

Requisitos: Windows 10/11, Python 3.10 o posterior con Tkinter y una webcam
compatible con OpenCV.

```bash
pip install -r requirements.txt
python hybrid_vision.py test      # autocomprobado, no toca la máquina
py -3 -m ruidavision.app          # abre la app
```

`test` valida el protocolo (vectores, swizzle, informe de posición) y la
homografía/detección sintéticas. Si pasa, lo que no depende del vídeo está bien.

Si ejecutas desde el código y ya existe un `calib.json` de una instalación, la
app reutiliza el de `%LOCALAPPDATA%\Ruida Vision\`. El CLI (`hybrid_vision.py`)
usa siempre el `calib.json` del proyecto.

### Actualizaciones (OTA)

La app **mira si hay versión nueva al arrancar** (a los ~0,8 s) y avisa en el
pie. Si aceptas, descarga el instalador en `%TEMP%`, cierra la app y lo ejecuta
en modo silencioso. La calibración de `%LOCALAPPDATA%` se conserva.

Si la actualización automática falla, descarga el instalador de Releases,
**cierra Ruida Vision** y ejecútalo a mano. El motivo del fallo queda en
`app.log`.

### Cámaras: índices

Los índices dependen de todos los dispositivos USB/cámara que Windows enumere.
En esta máquina, con la cámara virtual de Windows presente:

| índice | cámara | campo en `calib.json` |
|---|---|---|
| 1 | IMX179 cenital (tapa) | `top_cam` |
| 2 | IMX179 del cabezal (microscopio) | `head_cam` |

El índice `0` puede ser la **Windows Virtual Camera** u otro dispositivo; si
cambia algo, vuelve a listar:

```bash
py hybrid_vision.py scan
py hybrid_vision.py cams --top 1 --head 2
```

`scan` abre los índices 0..8 y guarda `scan_idxN.png`; mira esas fotos para
identificar cada cámara. Después pon los índices en **Ajustes** y desconecta y
vuelve a conectar (o reinicia la app). Si un dispositivo USB se añade o se
quita, la numeración puede desplazarse otra vez.

---

## Inicio rápido

1. Enchufa las dos cámaras y arranca la app. Comprueba en la cabecera que el
   panel contesta y que las cámaras abren.
2. En **Ajustes**, revisa IP, `top_cam`, `head_cam`, estacionamiento y FOV.
   Guarda y reconecta si cambiaste un índice.
3. En **Calibrar**, estaciona, congela una foto cenital y marca 4–6 puntos
   repartidos por la cama (esquinas incluidas). Ajusta H.
4. Mide el FOV del cabezal y, si hace falta, el offset del láser.
5. En **Marcas**, detecta los dos puntos y usa **Detectar y centrar los 2** o
   **Mover 1 / Mover 2**. Pasa las coordenadas a LightBurn.

Antes de cualquier movimiento: **láser deshabilitado**, recorrido libre y
LightBurn cerrado.

---

## Manual de usuario

La ventana tiene cuatro pestañas: **Cámaras**, **Calibrar**, **Marcas (Print and
Cut)** y **Ajustes**, con un tema oscuro de alto contraste. Abajo hay un
registro de 7 líneas y el botón **Ver registro**, que abre `app.log`.

La cabecera muestra la versión, el aviso **LASER APAGADO**, el estado del panel
(IP y si contesta) y el estado de las cámaras.

### Cámaras

Vista de las dos cámaras y los controles de movimiento manual.

- **Conectar cámaras** / **Desconectar**: abren o cierran las dos cámaras. La
  app intenta conectarlas sola al arrancar.
- **Buscar cámaras**: lista los índices detectados y guarda una foto de cada
  uno. Útil cuando Windows renumera.
- **Parar**: suelta las teclas del jog y corta el movimiento continuo. **No
  detiene el viaje nativo a coordenadas** (ver *Puntos clave*).
- **Origen 0,0**: viaje nativo a la esquina inferior izquierda. Confirma la
  llegada. Si no la confirma, la app bloquea más viajes en esa sesión.
- **paso (toque)**: con `−` / `+` (o las teclas `-` / `+`) cambias el paso sin
  soltar el WASD. Un **toque** da ese paso; **mantener** mueve en continuo.
  La rueda del ratón sobre el paso también lo cambia.
- **W A S D** o las **flechas**: movimiento manual. Con WASD la dirección se
  deduce de la homografía; si aún no hay H, se usa el sentido por defecto y
  `wasd_flip`. Las flechas usan una tabla fija (arriba = +Y, derecha = +X).
- Visores: **CENITAL** (con el área de trabajo y la posición del cabezal si hay
  H) y **CABEZAL** (con la cruz y la ventana de búsqueda del centrado).

### Calibrar

Aquí se calcula la homografía (píxel de la cenital ↔ milímetros de la máquina)
y se miden el FOV y el offset del láser.

Antes de empezar:

- Láser **apagado**, cama vacía y el cabezal sin la cápsula encima.
- Origen de la Ruida fijado en la **esquina inferior izquierda** y sin moverlo.
- LightBurn cerrado: el viaje nativo usa los puertos 40200/40207.
- `python ruida.py ping` debe devolver posiciones.

#### 1. Estacionar y congelar la foto

1. Pulsa **1. Estacionar** para apartar el cabezal al estacionamiento
   (`park`, por defecto 20,20 mm). Así el cabezal no tapa marcas en la foto.
2. Pulsa **Congelar foto**. La app para las cámaras, captura la cenital y las
   reconecta; la foto queda fija en pantalla. Los círculos verdes son
   **candidatos** del detector, no puntos de calibración todavía.
3. Si hace falta, ajusta **Umbral** (`0` = Otsu) y **Área mínima** y pulsa
   **Aplicar en foto**. Estos controles solo cambian la ayuda visual de esta
   pestaña; no cambian Print and Cut.

#### 2. Marcar los puntos

Con la foto congelada:

1. Mueve el cabezal con el jog o con **WASD** hasta ponerlo **físicamente
   encima** de una marca.
2. Sin mover más el cabezal, haz **clic en el centro de esa marca** en la foto
   congelada. La app guarda el píxel y la posición real del panel en ese
   instante.
3. Repite con **4 puntos como mínimo** (6 es mejor), bien repartidos por toda la
   cama y con las esquinas incluidas. Los candidatos del detector son solo una
   ayuda: puedes hacer clic en cualquier marca, aunque no tenga círculo.
4. Si un punto está mal, selecciónalo en la tabla y pulsa **Quitar punto** (o
   `Supr`). **Borrar puntos** limpia todo. **Marcar con numero** y
   **Renumerar** sirven para ordenar; los números deben quedar 1, 2, 3… sin
   huecos.

La foto no se actualiza mientras marcas: se congela una vez y se van añadiendo
puntos. El cabezal sí se mueve entre marcas.

#### 3. Ajustar la homografía

Pulsa **2. Ajustar H**. La app:

- Exige al menos 4 puntos y que la numeración sea 1..N.
- Ajusta `cv2.findHomography` con RANSAC.
- Guarda `H` y los puntos aceptados en `calib.json`.
- Muestra el error máximo y medio. **Si supera 1 mm, repite**: 0,1 mm de error
  aquí es 0,1 mm en cada marca.
- Avisa de los puntos descartados por RANSAC. No se guardan; si se repiten,
  probablemente el clic cayó en la marca de al lado.

Para comprobar sin mover nada:

```bash
python hybrid_vision.py run --no-move
```

#### 4. Medir el FOV del cabezal

Pulsa **3. Medir FOV**:

1. La app congela una foto de la cámara del cabezal.
2. Haz clic en los **dos extremos** de una distancia que conozcas de verdad
   (una regla o dos marcas separadas).
3. Escribe la distancia real en milímetros.
4. La app calcula `head_fov_mm` (ancho del campo de visión) y los mm/px, y lo
   guarda. También lo muestra en **Ajustes**.

Sin este valor el centrado fino puede no converger.

#### 5. Offset del láser

Pulsa **4. Offset laser**. Corrige que el ojo de la cámara y el punto del láser
no están en el mismo sitio:

1. Haz un disparo de prueba en una coordenada A (láser habilitado, con cuidado).
2. Centra el punto resultante con la cámara del cabezal.
3. Anota la coordenada P que da el panel ya centrada.
4. Introduce A y P. La app guarda `cam_offset_mm = A − P`.
5. Comprueba con un corte de prueba. Si el láser cae al otro lado de la marca,
   el signo está invertido: vuelve a medir/intercambia A y P.

#### Calibración por CLI

La misma homografía se puede hacer sin la GUI:

```bash
python hybrid_vision.py calibrate --points 6
```

Por cada punto: mueve el cabezal a una referencia con **WASD**, pulsa
**Enter** (el script lee la posición real de la Ruida), el cabezal se aparta
solo y haces **clic en el centro de la marca** en la foto cenital. Con 4+
puntos bien repartidos calcula la H con RANSAC.

- `--manual`: escribir X Y a mano si no hay informe de posición.
- `--no-watch`: no usar la cámara del cabezal como puntero.
- `--park X,Y`: cambiar el estacionamiento.
- `--top N`: cambiar el índice de la cenital.
- `--flip-mov`: dar la vuelta a W/S a mano solo si aún no hay H y el sentido
  sale raro.

El error se mide solo sobre los puntos que RANSAC acepta; los descartados se
cuentan aparte y no se guardan.

#### Comprobaciones de la máquina

```bash
py ruida.py magic archivo.rd   # número mágico; en esta máquina 0x88 (por defecto)
ping -n 3 <IP>                 # red y ruta (Windows)
python ruida.py ping           # handshake 50207 + 5 posiciones reales
```

`ping` correcto = coordenadas que cuadran con LightBurn. El canal 50207 no
manda nada solo: responde a un `0xCC` con el informe `A5 68` pegado.

### Marcas (Print and Cut)

Prepara la plantilla en el centro del área de trabajo, con el láser apagado y
LightBurn cerrado. El cabezal debe estar en una esquina (por ejemplo con
**Origen 0,0**) para que no tape una marca en la foto.

La hoja usa la misma barra de cámaras y los mismos visores grandes que
**Cámaras**: a la izquierda la foto cenital con las marcas detectadas y a la
derecha el cabezal en vivo. Tiene dos caminos: el automático y el paso a paso.

#### Opción A: Detectar y centrar los 2

1. Ajusta **marcas** (normalmente 2), **iteraciones** (4), **tolerancia mm**
   (0,1) y, si hace falta, **ROI mm x0,y0,x1,y1**.
2. Pulsa **Detectar y centrar los 2**. La app estaciona, mira la cama, descarta
   lo que cae fuera del área de trabajo, elige las dos marcas y lleva el
   cabezal a cada una con el viaje nativo. Después centra cada marca con la
   cámara del cabezal. El detector solo acepta **tags redondos con cruz
   dentro** (disco con cruz o aro con la cruz dentro); los discos lisos, aros
   sin cruz, cuadrados y texto de los diseños impresos se descartan aunque la
   hoja esté sin tapar.
3. Al terminar, las coordenadas reales quedan en `coords.txt`. Usa **Copiar
   coordenadas** o **Ver coords.txt**.

Es el camino más cómodo, pero mueve solo: deja el recorrido libre y el láser
apagado. El viaje nativo **no se puede cancelar desde la app**.

#### Opción B: paso a paso con Mover

1. **Detectar los dos puntos**: foto cenital, descarta lo que cae fuera de la
   cama y busca los dos **tags con cruz**. No mueve el cabezal. Las marcas se
   numeran 1 y 2 en la foto; los diseños impresos no compiten con ellas.
2. **Mover** (luego *Mover 1* / *Mover 2*): lleva el cabezal a cada marca con el
   viaje nativo, confirma la llegada y hace el centrado fino. El botón cambia
   solo de *Mover 1* a *Mover 2*.
3. Debajo aparece el **Punto a registrar en LightBurn**: la posición real del
   cabezal, no la calculada. Si el punto no quedó centrado, la app lo dice y
   **no da una coordenada**: una posición sin centrar dispara donde no toca.
4. Anota o copia el X/Y del punto 1, ve a LightBurn > Print and Cut, ponlo como
   primer punto y **desactiva el offset de la cámara**. La app ya lo tuvo en
   cuenta.
5. Vuelve, pulsa **Mover 2**, repite con el segundo punto y corta desde
   LightBurn.

Si no aparecen las dos marcas, revisa que la cruz se vea y que ninguna quede
tapada; ajusta **Umbral** y **Área mínima** en Ajustes o acota la búsqueda con
el **ROI**. Sirven tanto un disco con la cruz dentro como un aro con la cruz
dentro; evita que la cruz quede fuera del tag o partida por el umbral.

#### Coordenadas y LightBurn

- **Copiar coordenadas** copia el contenido de `coords.txt` al portapapeles.
- **Ver coords.txt** abre el archivo de la última tarea.
- En LightBurn, marca **2 puntos**, desactiva el offset de cámara y usa
  **Obtener coordenadas** para llevar el X/Y de cada punto.

Para validar la homografía sin mover nada: `run --no-move`.

```bash
python hybrid_vision.py run --marks 2 --debug --emit coords.txt
```

### Ajustes

Campos:

| campo | qué es |
|---|---|
| **IP de la Ruida** | IP de la controladora (por defecto 192.168.1.50). |
| **Indice camara cenital** | `top_cam`; en esta máquina, 1. |
| **Indice camara cabezal** | `head_cam`; en esta máquina, 2. |
| **Estacionamiento X,Y mm** | `park`; destino de **1. Estacionar** y del CLI. |
| **FOV cabezal mm** | `head_fov_mm`; lo mide **3. Medir FOV**. |
| **Marcas a detectar** | `marks`; normalmente 2. |
| **Umbral de negro (0=automático)** | `thr`; afecta a la detección de Print and Cut. Con `0` se adapta a la luz desigual de la cama. |
| **Area minima px** | `min_area`; afecta a la detección de Print and Cut. |
| **Desplazamiento X,Y mm** | `cam_offset_mm`; cámara menos láser. |

**Guardar ajustes** escribe en el `calib.json` de datos y recarga la
configuración. Si cambias el índice de una cámara, desconecta y vuelve a
conectar (o reinicia) para que se abra la correcta. **Listar camaras** hace un
`scan`.

### Controles de movimiento

- **W A S D** o las **flechas**: un paso por pulsación; mantener mueve en
  continuo. Abajo es `S` porque el origen está en la esquina inferior izquierda.
- **`-` / `+`**: suben/bajan el paso del toque (también `=` y el teclado
  numérico).
- **Rueda del ratón** sobre el paso: ±0,5 mm.
- El jog manual usa las teclas del panel (50207). **Parar** corta el jog, no el
  viaje nativo.
- Fuera de la app:

```bash
python ruida.py move 100 100
python ruida.py jog 2 -1
python ruida.py sniff
```

---

## Problemas frecuentes

### Las cámaras no aparecen o cambian de índice

1. Cierra otras apps que usen cámara (Teams, Zoom, navegador, LightBurn).
2. `py hybrid_vision.py scan` y mira `scan_idxN.png`.
3. Si el índice 0 es la **Windows Virtual Camera**, desactívala si no la usas:
   Administrador de dispositivos > Cámaras, o PowerShell elevado:
   `Get-PnpDevice -FriendlyName 'Windows Virtual Camera Device' | Disable-PnpDevice -Confirm:$false`.
4. Vuelve a poner `top_cam` / `head_cam` en Ajustes y reinicia la app.
5. Si un hub USB da error (Código 10), desconéctalo de la corriente y del USB,
   espera 10–15 s, conecta primero la alimentación y luego el USB. Prueba otro
   puerto del PC sin extensión y, si hace falta, un hub alimentado.

### El panel no contesta

- `ping <IP>` y `python ruida.py ping`.
- Revisa la IP en Ajustes y que el PC esté en la red de la Ruida.
- Cierra LightBurn y RDWorks: comparten los puertos 40200/40207.
- Revisa el firewall de Windows.

### La OTA no instala

- Descarga el instalador de Releases y ejecútalo con la app cerrada.
- Si Windows no deja cerrar la app, termínala desde el Administrador de tareas.
- Mira `app.log`: ahí queda la ruta del instalador, el tamaño descargado y el
  error.
- La app fuerza el cierre al lanzar el instalador; si aun así falla, el
  problema está en el instalador o en permisos, no en la descarga.

### El error de H es alto

- Añade puntos y repártelos por toda la cama, esquinas incluidas.
- Asegúrate de que el origen de la Ruida no se ha movido.
- No marques puntos fuera del área de trabajo: la homografía extrapola y no son
  medidas.
- Repite los puntos descartados; suelen ser clics en la marca de al lado.
- Si el error sigue >1 mm, vuelve a empezar la calibración.

### El centrado fino no converge

- Revisa iluminación y foco de la cámara del cabezal.
- Que la cápsula no tape la marca y que la marca esté entera en el FOV.
- Revisa `head_fov_mm` (mídelo con una regla) y `head_flip_x`/`head_flip_y`.
- La tolerancia fiable es **0,1 mm**; el paso mínimo del jog es ~0,2 mm, así que
  no pidas 0,05.
- Si el láser no cae en la marca, revisa `cam_offset_mm` y su signo.

### El cabezal se movió y la app no confirma

El viaje nativo no se puede cancelar desde la app. Comprueba físicamente que el
cabezal está detenido, usa el paro de la controladora si hace falta y reinicia
la app antes de volver a mover.

---

## Puntos clave

- **El viaje a las marcas usa el comando nativo** `D9 10 00 <X><Y>` por UDP
  50200 (el mismo que LightBurn *Move to Position > Go*), y confirma la llegada
  por la posición del panel 50207. Las coordenadas se recortan a la mesa
  (0..500 × 0..400 mm).
- **Ese viaje no se puede cancelar** ni desde la app ni desde el Stop de
  LightBurn: la controladora termina el desplazamiento. Ante una emergencia, usa
  el paro físico. Deja la trayectoria despejada y el láser deshabilitado.
- **La velocidad del viaje la fija el preámbulo** (`Ruida.PREGO`: `c9`, `c6 01`,
  `c6 21`), no el `D9`. Con preámbulo va a más de 300 mm/s; sin él, a ~10 mm/s.
  No se acelera con `set_param`, que no cambia nada.
- **Cierra LightBurn antes de mover**: el 50200 usa el puerto origen 40200, el
  mismo que RDWorks. El 50207 usa el 40207.
- **No combines teclas** para movimiento automático: dos teclas a la vez no dan
  una trayectoria interpolada fiable.
- **Las cámaras hay que abrirlas pidiendo MJPG** (fourcc + resolución + fps en el
  constructor). Pedirlo con `set()` después deja el driver en YUY2: 8 s en abrir
  y 2 fps en vez de 0,9 s y 24,9 fps.
- **El paso mínimo es ~0,2 mm** (desplazamiento fijo, no latencia). Por eso la
  tolerancia de centrado fiable es **0,1 mm**, no 0,05.
- **Marca 0,1 mm de error de homografía como 0,1 mm en el producto**: es la
  fuente de error principal, seguida de `head_fov_mm`.
- **La app instalada guarda en `%LOCALAPPDATA%\Ruida Vision\calib.json`**; el
  CLI usa el `calib.json` del proyecto. Si ejecutas la app desde el código y ya
  existe el de la instalación, la app reutiliza ese archivo.

### Seguridad

- Este código **nunca manda órdenes de corte ni de movimiento del eje Z**
  (no toca `0xA8`/`0xA9`). Mueve la cabeza, no el láser.
- Potencia, aire o corte se ajustan en LightBurn, no aquí.
- Prueba siempre con el láser **deshabilitado** y la pieza ya colocada.
- `run --no-move` calcula coordenadas sin mover nada.

### Límites conocidos

- **`find_marks` es "componentes oscuros de área plausible"**: detecta puntos y
  cruces, pero no distingue un punto de una letra. Si hay falsos positivos,
  ajusta `--thr`/`min_area` o añade un filtro de redondez.
- **`head_fov_mm` es una constante**, no una calibración: mídela con `--manual`
  contra una rejilla conocida si el centrado fino no converge.
- La escala del cabezal puede ir **girada o espejada**: si jugando al jog la
  marca se aleja en vez de acercarse, ajusta `head_flip_x`/`head_flip_y` en
  `calib.json`.
- Los `ACK`/`RSP` varían por firmware: se aceptan `0xC6`/`0xCC` como ACK y
  `0x46`/`0xCF` como error.
- `run` asume que nadie más toca la máquina.

---

## Historial de cambios

Las notas de cada versión están en
[GitHub Releases](https://github.com/JesPezz/ruida-vision/releases). El diario de
pruebas con la máquina está en `coplitovs-notas.md`.

---

## Licencia

GPLv3 o posterior (**`GPL-3.0-or-later`**). El texto completo está en
[LICENSE](LICENSE).
