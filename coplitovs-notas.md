# Notas para OpenCode y la siguiente sesión

## Estado de Print and Cut

La conexión de movimiento nativo ya se probó con la máquina física. En la
última prueba, **Mover 1** llevó el cabezal a la primera marca detectada. La
posición no quedó centrada como se esperaba; antes de atribuirlo al movimiento,
hay que repetir la calibración y descartar un error en la homografía/offset.

También se detectó un problema pendiente con la segunda marca: el detector la
clasificó fuera del área de trabajo y no la trató correctamente como objetivo.
No se ha diagnosticado todavía si la causa está en la calibración, la
homografía, el ROI, el frame/cámara o la marca detectada. No relajar los límites
de seguridad para hacer que el punto pase; revisar las coordenadas y la
calibración primero.

## Movimiento nativo observado

- La captura local `ruida_lan_clean.pcap` muestra LightBurn **Move > Move to
  Position > Go** hacia UDP 50200. La captura no se incluirá en el remoto.
- Los datagramas se decodifican como `D9 10 00 <X:5 bytes><Y:5 bytes>`.
- Tres destinos de la captura coincidieron con coordenadas que el usuario
  recuerda haber introducido: `(183.010, 80.000)`, `(199.740, 349.280)` y
  `(419.960, 349.278)` mm. El usuario confirmó que LightBurn movió el cabezal a
  esos destinos.
- El proyecto genera el mismo datagrama y verifica la posición vía el canal
  50207. La primera prueba física desde Print and Cut confirmó que Mover 1
  alcanza el primer objetivo.
- Este no es un jog por teclas ni los opcodes `0x88`/`0x89` sueltos. No volver a
  habilitar los jogs XY temporizados/simultáneos: las pruebas anteriores
  sobrepasaron destinos y no interpolaron de forma fiable.
- El viaje nativo no se puede cancelar desde la app ni mediante Stop de
  LightBurn; no cerrar la app ni enviar otro viaje durante una orden activa.
  Dejar despejado el recorrido y tener accesible el paro físico. Cerrar
  LightBurn antes de usar Ruida Vision porque ambas aplicaciones necesitan el
  puerto local 40200.
- Los destinos están limitados a la caja medida de 0..500 × 0..400 mm.
  `Origen 0,0` usa ahora el viaje nativo de destino fijo y confirma llegada;
  `Estacionar` en Calibrar y **Detectar y centrar los 2** siguen deshabilitados.
  Print and Cut avanza un punto por pulsación.

## Próximo trabajo

1. Respaldar la calibración actual sin sobrescribir el `calib.json` de usuario
   durante pruebas.
2. Repetir la calibración con puntos bien repartidos por toda la cama y revisar
   error de reproyección, orientación y offset de cámara.
3. Repetir detección sin movimiento. Registrar para cada marca píxeles,
   coordenadas en mm, ROI y razón del rechazo; comparar la segunda marca con el
   área segura y con `coords.txt`.
4. Asegurar que visor, coordenadas mostradas y destino enviado son exactamente
   la misma pareja seleccionada.
5. Solo después de resolver la detección/calibración, probar Mover 1 y Mover 2
   separadamente, con láser deshabilitado y recorrido despejado. No relajar
   `Panel.SAFE` para ocultar detecciones fuera de cama.

## Diagnóstico offline del selector (30 de septiembre)

- Se analizó el `bed.png` y el `calib.json` activos en `%LOCALAPPDATA%\Ruida
  Vision`, sin abrir cámaras ni mover la máquina. La homografía activa reproyecta
  sus seis puntos con error medio de 0.082 mm y máximo de 0.202 mm.
- En esa imagen, dos discos de las marcas tienen componentes de 493 y 483 px;
  además aparece una mancha/reflejo de 61 px que la homografía coloca dentro de
  la caja segura. El selector anterior elegía el par más separado, por lo que
  podía sustituir una marca por ese reflejo.
- `pick_pair` ahora prioriza el área de componente de las dos marcas dentro del
  área segura y usa la distancia como desempate. Se conserva el filtro de
  seguridad; no se amplían los límites ni se cambia la calibración de usuario.
- Pendiente: repetir **Detectar** sin movimiento y confirmar en la foto que las
  dos retículas caen sobre los discos y que las coordenadas mostradas son las
  correspondientes. Esta corrección offline no confirma aún la detección con
  un fotograma nuevo ni el segundo viaje físico.

## Ruido en la ayuda visual de Calibrar (30 de septiembre)

- La foto activa de la cama produce 29 componentes con el umbral global: los dos
  discos de referencia son redondos (25 px de diámetro), pero la malla, los
  reflejos y bordes también aparecen como candidatos. Esta lista solo ayuda a
  ajustar el clic; los puntos de calibración siguen siendo capturas manuales.
- La foto congelada de la cenital ahora resalta solo componentes de área mínima
  40 px, circularidad mínima 0.55 y relación de aspecto máxima 1.5. Se mantienen
  el clic libre, el snap al candidato cercano y la homografía existentes.
  Validar con una foto nueva y asegurar que cada marca de calibración tenga
  forma redonda y contraste claro.
- Si existe una homografía previa, los candidatos de esa vista se comparan con
  la caja segura estimada de la máquina, pero los que queden fuera no se
  descartan: se muestran en ámbar y se registran como diagnóstico. Son puntos
  potencialmente válidos para calibrar aunque no sean destinos seguros de viaje.
  Sin homografía, todos los candidatos de forma válida se muestran en verde.
- La captura de pantalla local `Captura de pantalla 2026-09-30 155224.png`
  muestra tres marcas verdes sobre la cama, tres discos oscuros sobre tarjetas
  blancas sin retícula y candidatos ámbar en la guía lateral. Con `thr=0`,
  `find_marks` usa Otsu global: omite discos grises con poco contraste y aun
  detecta agujeros/reflejos circulares de la guía. No subir el umbral global sin
  probarlo en toda la imagen: aumenta mucho la textura candidata y también
  cambia la detección de Print and Cut. Para esta calibración se pueden marcar
  manualmente los centros no resaltados; el clic es libre si no cae cerca de
  otro candidato. Preferir iluminación difusa uniforme y puntos negros mate
  sobre fondo blanco mate antes de cambiar el umbral del flujo de producción.
- La calibración de LightBurn no se copia directamente: su alineación de cámara
  es interna a LightBurn y esta app guarda su propia homografía píxel→mm de
  máquina. Puede servir como referencia visual/procedimiento, pero la app debe
  medir su propia   relación con la posición reportada por la Ruida.

## Controles de Calibrar y viaje a Origen

- `Origen 0,0` está habilitado únicamente para la orden nativa de coordenadas:
  destino fijo (0,0), verificación de llegada y bloqueo fail-closed si la
  controladora no confirma. Como con Mover 1/2, no se puede cancelar desde la
  app; no enviar otra orden mientras el viaje esté activo. El jog activo debe
  soltarse antes de solicitar el viaje.
- La velocidad no forma parte del paquete `D9 10`: la determina el perfil/estado
  de la controladora. El usuario informó que, después de abrir LightBurn e
  intentar ir al origen, la velocidad de los viajes volvió a ser razonable. No
  se ha aislado qué estado cambió; no añadir un supuesto parámetro de velocidad
  al datagrama.
- Calibrar ofrece un pad compacto de jog direccional con la misma semántica de
  Vivo, paso visible compartido y rueda con saltos de 0.5 mm. Los visores en vivo
  cenital/cabezal mantienen mayor espacio de pantalla al reducir el tamaño del
  control. Print and Cut también tiene jog manual y un visor vivo ampliado del
  cabezal (panel de 360 px, con retícula central resaltada), junto a la foto de
  detección y las coordenadas. El botón de conexión está en Calibrar; Print and
  Cut reutiliza el stream ya conectado.
- Después de Detectar, el stream previo de cámaras se restaura para que el visor
  del cabezal de Print and Cut siga actualizándose.
- La foto congelada admite umbral (0 = Otsu) y área mínima configurables y
  reaplica la detección sobre esa misma imagen. Los valores son locales a la
  vista de calibración y no alteran el detector de Print and Cut ni `calib.json`.
- La reconexión se ejecuta al terminar la captura incluso si esta lanza error;
  el error se muestra en la pestaña y se conserva en el registro.
- Última medición reportada tras ajustar la homografía: residuo máximo 0.309 mm,
  medio 0.172 mm. La nueva interfaz aún requiere validación física de jog,
  reconexión, velocidad y Origen; las pruebas automatizadas son offline y no
  mueven el cabezal.
- Corrección del usuario (30 de septiembre): los datos de los dos tests
  compartidos antes estaban mal; no derivar de ellos el signo o la aplicación
  del offset. Se revirtió la última modificación que movía la corrección de
  `cam_offset_mm` del punto 1 al punto 2. Después se aclaró que las cifras de
  `coords.txt` ya incluyen el offset mientras que la llegada nativa y las
  coordenadas verdes corresponden a otro paso del flujo.
- Datos nuevos reportados para Print and Cut:
  - Detectadas: M1 `(292.373, 313.670)`, M2 `(214.466, 315.311)`.
  - Verdes al mover: P1 `(341.506, 312.232)`, P2 `(312.730, 312.436)`.
  - Reales: M1 `(341.274, 310.746)`, M2 `(263.817, 312.169)`.
  - Diferencias verde - real: P1 `(+0.232, +1.486)` mm; P2
    `(+48.913, +0.267)` mm. Las coordenadas reportadas no concuerdan
    directamente en la lista porque usan pasos diferentes de la conversión de
    offset. El usuario confirmó que los rótulos verdes son de esos movimientos
    y que el tratamiento actual del offset es correcto. La diferencia residual
    de centrado de la marca se investiga por separado, no cambiando el offset.
- Aclaración aceptada por el usuario: `cmd_run --no-move` entrega las marcas
  detectadas sin offset como destinos nativos, mientras `save_txt` suma
  `cam_offset_mm` a las coordenadas de `coords.txt`. Por tanto, el log de
  detección y el rótulo verde no muestran necesariamente la misma representación
  de coordenadas; la conversión previa de `_fin_punto` se considera correcta y
  quedó restaurada. No cambiarla por las primeras comparaciones erróneas.
- **Pendiente para la siguiente sesión:** estudiar cómo centrar automáticamente
  el campo de visión de la cámara del cabezal sobre el centro de la marca circular
  después de cada llegada. La diferencia observada no es constante entre
  ejecuciones; medir el centro detectado por frame, el offset fino aplicado y el
  error final por iteración antes de cambiar calibración u homografía. Usar
  pruebas offline/sintéticas primero y realizar movimiento físico solo con
  confirmación explícita del usuario.

## Cambios locales de esta sesión

- `ruida.py`: reproduce el `D9 10` de LightBurn, valida área segura y espera
  confirmación de posición por 50207; no reintenta un movimiento si no llega ACK.
  `Panel.move_to` continúa deshabilitado para movimiento por jog temporalizado.
- `hybrid_vision.py`: mantiene un canal Ruida 50200 aparte para viajes nativos;
  `Machine.goto()` del workflow automático sigue bloqueado y la acción explícita
  `goto_native()` se usa para Print and Cut.
- `ruidavision/app.py`: habilita Mover 1/2 con el viaje nativo; muestra que no es
  cancelable, no permite parar/cerrar mientras está activo y bloquea más viajes
  si la llegada no se confirma.
- `ruidavision/prueba_app.py`: comprueba datagramas capturados, límites,
  secuencia de los dos objetivos, error fail-closed y aviso de Stop.
- `README.md` y `.github/copilot-instructions.md`: documentan el comando
  observado, restricciones de seguridad y estado de pruebas.
- Verificación ejecutada: `py -3 ruida.py test`, `py -3 hybrid_vision.py test`,
  `py -3 -m ruidavision.prueba_app` (73 comprobaciones) y `py -3 -m py_compile
  ruida.py hybrid_vision.py ruidavision\app.py ruidavision\prueba_app.py`;
  pasaron. Estas verificaciones no sustituyen la nueva calibración ni la prueba
  física de Mover 2.

## Publicación

El usuario pidió publicar los cambios al remoto. En esta sesión el ejecutable
`git` no aparece disponible en PATH ni en las ubicaciones estándar inspeccionadas,
por lo que no se pudo comprobar el estado Git, crear el commit ni hacer push.
Antes de publicar, revisar `git status` y excluir los archivos locales de
calibración, imagen y captura (`calib.json`, `bed.png`, `ruida*.pcap*`,
`scan_idx*.png`, `coords.txt`) si aparecen como cambios; no revertirlos.
