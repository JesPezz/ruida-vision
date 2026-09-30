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
  `Origen 0,0`, `Estacionar` y **Detectar y centrar los 2** siguen
  intencionalmente deshabilitados. Print and Cut avanza un punto por pulsación.

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
