# Notas para la próxima sesión

Estado: `py hybrid_vision.py calibrate --park 20,20` **arranca y mueve el cabezal**,
pero la cosa no está para calibrar todavía. Cuatro problemas reportados por el usuario,
por orden de gravedad. Nada de esto está arreglado aún.

Los tests siguen pasando (`py -u hybrid_vision.py test`, 10 líneas OK) porque ninguno
cubre la interacción real: el lag, el resize y el sentido del movimiento solo se ven
con la máquina delante.

---

## 1. Se mueve a la inversa

`LETRAS = {"w": "+Y", "a": "-X", "s": "-Y", "d": "+X"}` (hybrid_vision.py:281) está
puesto a mano desde coordenadas de máquina, no desde lo que se ve en pantalla.

El origen de la máquina está en la esquina inferior izquierda, pero **eso no dice nada
de cómo está montada la cenital**: si la cámara va girada 180°, la Y de la máquina
apunta hacia abajo en la imagen y "arriba" en pantalla es `-Y`. Con W a `+Y` el cabezal
se mueve justo al revés de lo que espera el ojo. Encaja con el síntoma: el usuario no
dijo "un eje va mal", dijo **a la inversa**, o sea los dos.

**Arreglo propuesto: deducir el sentido de `H`, no escribirlo a mano.** Así el mapeo
sale siempre de lo que se ve en la cenital y no hay que volver a tocarlo si un día se
gira la cámara:

```python
# El sentido de WASD sale de la homografia, para que coincida con lo que se ve
# en la cenital en vez de con los ejes de la maquina. Se pregunta a H por donde
# caen "derecha" y "arriba" en la imagen y se toma ese signo.
def _wasd_de_H(H, cx=960, cy=540):
    p0 = _mm(H, cx, cy)
    der = _mm(H, cx + 50, cy)      # derecha en la imagen
    arr = _mm(H, cx, cy - 50)      # arriba en la imagen (py crece hacia abajo)
    eje_x = "+X" if der[0] >= p0[0] else "-X"
    eje_y = "+Y" if arr[1] >= p0[1] else "-Y"
    return {"d": eje_x, "a": _op(eje_x), "w": eje_y, "s": _op(eje_y)}
```

Fallback si no hay `H` (primera calibración, que es justo cuando hace falta): dejar las
letras como están ahora pero **mas un `--flip-mov` o parecido** para darle la vuelta,
que es una línea y no obliga a adivinar. Alternativa aún más barata para desbloquear:
probar a mano la combinación que se ve bien y escribirla, pero entonces hay que saber
si el giro es 180° (los dos ejes) o un espejo (uno solo). **Eso es lo primero que hay
que mirar en la próxima sesión**: pulsar W y ver hacia dónde va en la cenital.

## 2. Mucho lag, va lento y de pronto se cuelga

Son tres cosas sumadas, y la primera es la que más pesa:

**a) `grab()` lee 3 fotogramas y les hace un Laplaciano a cada uno** (hybrid_vision.py:104),
y `mirar` lo llama en **las dos cámaras** en cada vuelta (líneas 388 y 416). A
1920x1080 con `cv2.CV_64F` cada Laplaciano son ~16 MB de ida y vuelta, y eso tres
veces por cámara y seis por vuelta, más la conversión a gris de cada fotograma. Ahí
está el lag, y es también el candidato número uno a los cuelgues: es una barbaridad de
memoria y de tiempo para elegir "el fotograma menos borroso" en un lazo que se repite
cada 30 ms.

La elección del más enfocado tiene sentido para `click_point`, que busca una marca
quieta en un fotograma suelto; para **navegar con WASD no hace falta**: el ojo perdona
que la imagen esté algo borrosa mientras movemos. Arreglo: en `mirar` usar un
`read()` pelado (un fotograma, sin Laplaciano) y dejar `grab` donde de verdad importa.

**b) `m.pos(0.6)` (línea 396) puede tardar 0.6 s** y va en el mismo hilo que el dibujo.
Ya se limitó a dos veces por segundo, pero sigue metiendo hasta 600 ms de tapón de golpe.
`Panel._frame` (ruida.py:208) además late cada segundo. Para un visor que solo necesita
una posición orientativa, 0.6 s de timeout es mucho: bajarlo, o moverlo a un hilo con
`threading` y leer la última conocida sin bloquear.

**c) `Panel.hold` (ruida.py:299) hace `time.sleep(ms/1000)` en el hilo de la interfaz.**
Un paso de 3.4 mm son 100 ms de congelación por tecla. Con la repetición del teclado
eso se acumula y da la sensación de que se cuelga. Lo mismo: hilo para el jog, o al
menos no bloquear el lazo de `imshow`/`waitKey`.

Nota aparte: `cv2.imshow` de un compuesto de 2566x720 cada vuelta también cuesta, pero
si se arregla (a) y se sube un poco el `waitKey` de 30 ms, debería sobrar.

## 3. La ventana no se redimensiona bien en Windows

`cv2.namedWindow(win, cv2.WINDOW_NORMAL)` (hybrid_vision.py:371) permite redimensionar,
pero como se le pasa **un `imshow` de tamaño fijo cada vuelta**, va y viene: el backend
de Windows reajusta la ventana al tamaño de la imagen y pelea con lo que haga el usuario.
Es el mismo patrón que `click_point`, que usa el `namedWindow` por defecto (autosize,
línea 178) y por eso no da guerra.

Arreglo: o `WINDOW_AUTOSIZE` y se escala el contenido al tamaño que convenga, o
`WINDOW_NORMAL` de verdad pero **escalando la imagen ya compuesta al tamaño actual de
la ventana** antes de cada `imshow` (con `cv2.getWindowImageRect` para saberlo). Lo
primero es más simple y probablemente suficiente.

## 4. La interfaz es poco amigable

Queja legítima: ahora mismo son dos cámaras pegadas sin decir cuál es cuál, la ayuda
en una linea diminuta abajo, y nada que indique qué está pasando (si el panel contesta,
si el punto se ha cogido, hacia dónde va el cabezal).

Mínimos que pide el usuario sin que se lo pidamos nosotros:
- **Etiqueta en cada mitad**: "CABEZAL" y "CENITAL" bien visibles.
- **Estado del panel**: si la Ruida no contesta en 50207, decirlo en pantalla, no solo
  por consola (ahora sale un AVISO al principio y luego silencio).
- **Progreso**: "punto 3/6" en la ventana, no solo en el título.
- **Flecha/indicador del sentido del movimiento** en la cenital, que es justo lo que
  ayudaría a ver de un vistazo si el eje va al revés (problema 1).
- **Botones/ayuda más grandes**: la escala 0.5 actual no se lee.

## 5. Pendiente de antes (sigue en pie)

- El punto malo (px(541,46)=mm(0,0)) sigue en `cfg["points"]`; es cosmético, `run` solo
  usa `H` y `calibrate` sobrescribe `points`.
- Tras calibrar: `run --iters 1` para comprobar los signos `head_flip_x`/`head_flip_y`
  con el láser apagado.
- Si algún día hace falta un teclado que no dependa del foco: hook Win32 con `ctypes`
  (`SetWindowsHookEx(WH_KEYBOARD_LL)`) + bucle de mensajes. No implementado, y con
  WASD ya no hace falta.

---

## Orden sugerido para la próxima sesión

1. **Arreglar el lag** (2a primero: `read()` pelado en `mirar`; luego 2b y 2c). Sin
   esto, cualquier otra prueba es incómoda y el usuario no puede ni calibrar.
2. **Mirar el sentido del movimiento** con W y decidir el arreglo de (1) — si el giro
   es de 180°, el mapeo por `H` lo coge solo.
3. **Resize** (3), que es media hora.
4. **Interfaz** (4), que es lo que hace que las tres anteriores se puedan usar a gusto.

## Cómo probar

```
py hybrid_vision.py calibrate --park 20,20
```

Ventana con las dos cámaras. W A S D mueven, un paso por pulsación; `v` cicla el paso
entre 0.2 / 0.44 / 3.4 mm; Enter acepta el punto; `q` sale. Si sale una línea
`tecla 0x... (…) sin asignar`, ese es el código crudo de una tecla que falta en la tabla.

Espejo de trabajo: `/root/ruida-vision` (mismo `hybrid_vision.py` y `README.md`,
sincronizar con `cp` + `cmp` y borrar `__pycache__`).
