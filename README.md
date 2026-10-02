# Ruida Vision — visión híbrida para láser CO₂ (Ruida RDC7132G)

Posiciona y alinea **marcas de registro (Print and Cut)** con dos cámaras USB
IMX179: una **cenital** en la tapa (visión general de la cama) y otra en el
**cabezal** (microscopio, ajuste fino). Habla con la controladora por UDP y deja
las coordenadas listas para pegarlas en LightBurn.

El PC que ejecuta la app es el mismo al que están enchufadas las cámaras y debe
estar en la red de la Ruida. No hay Raspberry en este proyecto.

```
ruida.py          red: mover el cabezal y leer su posición real (sin dependencias)
hybrid_vision.py  cámaras, detección de marcas, homografía y flujo Print and Cut
ruidavision/      la app de escritorio (Tkinter); se arranca con ruidavision.app
calib.json        se crea solo en `calibrate`: cámaras, FOV, homografía H, puntos
```

---

## Instalación y arranque

```bash
pip install -r requirements.txt
python hybrid_vision.py test      # autocomprobado, no toca la máquina
```

`test` valida el protocolo (vectores, swizzle, informe de posición) y la
homografía/detección sintéticas. Si pasa, lo que no depende del vídeo está bien.

Abrir la app desde el código (Windows):

```powershell
py -3 -m ruidavision.app
```

La app **mira si hay versión nueva al arrancar** (a los ~0,8 s) y avisa en el
pie; instalar sigue siendo cosa tuya. La instalación y la versión de código
comparten `%LOCALAPPDATA%\Ruida Vision\calib.json` cuando existe. El registro
está en `%LOCALAPPDATA%\Ruida Vision\app.log` (botón **Ver registro**).

Índices de cámara (pueden cambiar si se conecta algo nuevo; confírmalos con
**Buscar cámaras** / `py hybrid_vision.py scan`):

| índice | cámara | config |
|---|---|---|
| 0 | IMX179 del cabezal (microscopio) | `head_cam` |
| 1 | Galaxy A56 vía Enlace de Windows — descartada | — |
| 2 | IMX179 cenital (tapa) | `top_cam` |

Si desconectas el Galaxy, los índices se recomponen: vuelve a pasar `scan` y
actualiza `top_cam` / `head_cam` en **Ajustes**. Para probar sin editar nada:
`py hybrid_vision.py cams --top 2 --head 0`.

---

## Manual de usuario

La ventana tiene cuatro pestañas: **Vivo**, **Calibrar**, **Marcas (Print and
Cut)** y **Ajustes**.

### Vivo

Vista de las dos cámaras y los controles de movimiento manual.

- **Conectar cámaras** / **Desconectar**: abren o cierran las dos cámaras.
- **Buscar cámaras**: lista los índices detectados (útil si cambian).
- **Parar**: suelta las cuatro teclas del panel y corta el jog. **No detiene el
  viaje nativo a coordenadas** (ver *Puntos clave*).
- **Origen 0,0**: viaje nativo al origen (esquina inferior izquierda).
- **paso (toque)**: con los botones `−` / `+` (o las teclas `-` / `+`) cambias el
  paso sin soltar el WASD. Un **toque** da ese paso; **mantener** mueve en continuo.

### Calibrar

Aquí se calcula la homografía (la relación píxel de la cenital ↔ milímetros de
la máquina) y se revisan los visores.

- **1. Estacionar**, **Congelar foto**, **Borrar puntos**, **Quitar punto**,
  **2. Ajustar H**, **3. Medir FOV**, **4. Offset laser** recorren la rutina.
- Con una foto congelada puedes cambiar **Umbral** (`0` = Otsu) y **Área
  mínima**, y **Aplicar en foto**. Estos controles solo afectan a la ayuda
  visual de esta pestaña: no cambian Print and Cut.
- La hoja enseña a la vez las dos cámaras en vivo y la foto congelada.

**Calibrar la homografía** (con el láser apagado y la cama vacía):

```bash
python hybrid_vision.py calibrate --points 6
```

Por cada punto: mueve el cabezal a una referencia con **WASD**, pulsa **Enter**
(el script lee la posición real de la Ruida), el cabezal se aparta solo y haces
**clic en el centro de la marca** en la foto cenital. Con 4+ puntos bien
repartidos (esquinas incluidas) calcula `cv2.findHomography` con RANSAC.

- **Si el error supera 1 mm, repite**: 0,1 mm de error aquí es 0,1 mm en cada
  marca. El error se mide solo sobre los puntos que RANSAC acepta; los
  descartados se cuentan aparte y no se guardan.
- Sin informe de posición: `--manual` para escribir X Y a mano.

Antes de calibrar, dos comprobaciones de una sola vez:

- **Número mágico** (`py ruida.py magic archivo.rd`): en esta máquina el valor
  correcto es **`0x88`** (el de por defecto), así que no hace falta pasar
  `--magic`. Solo hay que repetir el paso si se cambia de controladora.
- **Origen de la Ruida**: fíjalo en la **esquina inferior izquierda de la cama**
  y **no lo muevas** entre calibrar y cortar. La homografía incluye ese desfase.

Comprobar la red:

```bash
ping -c3 <IP>          # red y ruta
python ruida.py ping   # handshake 50207 + 5 posiciones reales
```

`ping` correcto = coordenadas que cuadran con LightBurn. El canal 50207 no manda
nada solo: responde a un `0xCC` con el informe `A5 68` pegado.

### Marcas (Print and Cut)

1. **Detectar los dos puntos**: foto cenital, descarta lo que cae fuera de la
   cama y elige las dos manchas de mayor área.
2. **Mover** (luego *Mover 1* / *Mover 2*): lleva el cabezal a cada marca con el
   viaje nativo y confirma la llegada. Se anotan a mano las coordenadas que da
   el cabezal.
3. **Detectar y centrar los 2**: hace todo seguido —estaciona, mira la cama,
   lleva el cabezal a cada marca y la recentra.

Marca **2 puntos** y deja el **offset de LightBurn desactivado**: aquí ya se ha
tenido en cuenta. Escribe las coordenadas con **Obtener coordenadas** en
LightBurn y corta desde ahí.

Para validar la homografía sin mover nada: `run --no-move`.

```bash
python hybrid_vision.py run --marks 2 --debug --emit coords.txt
```

### Ajustes

IP de la Ruida, índices de las cámaras, estacionamiento, FOV del cabezal, marcas
a detectar, umbral, área mínima y desplazamiento. Al cambiar el índice de una
cámara hay que desconectar y volver a conectar (o reiniciar la app).

### Controles de movimiento

- **W A S D** (no las flechas): un paso por pulsación. `v` cambia el tamaño del
  paso. Abajo es `S` porque el origen está en la esquina inferior izquierda.
- **`-` / `+`**: suben/bajan el paso del toque.
- El jog manual usa las teclas del panel (50207). Fuera de la app se prueba con
  `python ruida.py move 100 100`, `python ruida.py jog 2 -1` y
  `python ruida.py sniff`.

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
