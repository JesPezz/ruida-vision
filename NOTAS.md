# Notas para la próxima sesión

Estado: los **cuatro problemas reportados están arreglados y con tests que los cubren**
(`py -u hybrid_vision.py test`, 13 líneas OK). Lo que queda es lo que solo se puede
comprobar con la máquina delante.

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

**(c) `Panel.hold` con `time.sleep` (ruida.py:299) — PENDIENTE, sin tocar.** Un paso de
3.4 mm son 100 ms de congelación por tecla en el hilo de la interfaz. En la CLI se nota
poco; cuando llegue la app GUI hay que hacerlo con un hilo (`threading`) que mande el
jog y devuelva el control al lazo de dibujo. **No se ha tocado `ruida.py`.**

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

- `py hybrid_vision.py calibrate --park 20,20` y mirar que **W va hacia arriba** en la
  cenital. Con la `H` actual tiene que salir así solo; si no, `--flip-mov`.
- `run --iters 1` con el **láser apagado** para comprobar los signos `head_flip_x`/`y`.
- El punto malo (px(541,46)=mm(0,0)) sigue en `cfg["points"]`; es cosmético, `run` solo
  usa `H` y `calibrate` sobrescribe `points`.

## 6. Lo siguiente: la app GUI

Pendiente de lo que pidió el usuario después: GUI, instalador de Windows y actualización
OTA por release de GitHub. **Nada de eso está escrito todavía** — que quede clarísimo
porque ya pasó dos veces que se di por hecho. Al hacerlo, ojo al punto 2c (el jog tiene
que ir en un hilo) y a que el clic de calibración se guarde con su posición real, no en
`(960,540)`.

## Cómo probar

```
py hybrid_vision.py test                                  # 13 líneas, sin máquina
py hybrid_vision.py calibrate --park 20,20                # ventana con las 2 cámaras
```

W A S D mueven, un paso por pulsación; `v` cicla el paso entre 0.2 / 0.44 / 3.4 mm; Enter
acepta el punto; `q` sale. Si sale `tecla 0x... sin asignar`, ese es el código crudo de
una tecla que falta en la tabla.

Espejo de trabajo: `/root/ruida-vision` (mismo `hybrid_vision.py` y `README.md`,
sincronizar con `scp -O` + `cmp` y borrar `__pycache__`).
