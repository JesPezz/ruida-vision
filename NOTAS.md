# Notas para la próxima sesión

Estado: los **cuatro problemas reportados están arreglados y con tests que los cubren**
(`py -u hybrid_vision.py test`, 13 líneas OK). Además ya está la **app de escritorio**
(`ruidavision/`: GUI, instalador de Windows y OTA) y el jog dejó de ir a tirones —
mantener la tecla o el botón mueve en continuo, un toque corto da un paso fino. Lo que
queda es lo que solo se puede comprobar con la máquina delante.

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
- Publicar la **release v1.1** en GitHub: el tag `v1.0` apunta un commit por detrás de
  `main` y no hay release publicada, así que el OTA todavía no encuentra nada. Bumpear
  `VERSION` en `ruidavision/__init__.py` antes de empaquetar.
- El techo de **5 mm/s es del perfil de LightBurn** ("Config maquina jog lento"); no se
  puede cambiar desde aquí porque `set_param` no hace nada (README 319-323).

## Cómo probar

```
py hybrid_vision.py test                                  # 13 líneas, sin máquina
python ruida.py test                                      # protocolo del panel, sin máquina
py hybrid_vision.py calibrate --park 20,20                # ventana con las 2 cámaras

# App de escritorio (en la Pi hace falta xvfb-run: no tiene escritorio real)
/root/venv/bin/python -m ruidavision.prueba_app           # 20 comprobaciones de GUI y jog
```

W A S D mueven, un paso por pulsación; `v` cicla el paso entre 0.2 / 0.44 / 3.4 mm; Enter
acepta el punto; `q` sale. Si sale `tecla 0x... sin asignar`, ese es el código crudo de
una tecla que falta en la tabla.

Espejo de trabajo: la **Raspberry Pi** `root@192.168.1.107:/root/ruida-vision` (mismo
commit que el PC). Para probar la GUI hay que usar `/root/venv/bin/python` (el `python3`
del sistema no tiene `cv2`) y `xvfb-run -a` (la Pi no tiene escritorio). El espejo viejo
del PC (`/root/ruida-vision`) se borró: era el commit base, sin los arreglos.
