# Repository guidance

## Build, test, and lint

Run commands from the repository root on Windows. The project targets the Windows PC connected to the USB cameras; the Ruida controller is reached over the local network.

- Install Python dependencies: `py -3 -m pip install -r requirements.txt`
- Vision/protocol self-tests (synthetic; no camera or controller): `py -3 hybrid_vision.py test`
- Ruida protocol self-test (no network/controller): `py -3 ruida.py test`
- OTA updater self-test: `py -3 -m ruidavision.actualizar test`
- Desktop GUI smoke suite: `py -3 -m ruidavision.prueba_app` (requires a working Tk display; it fakes cameras, machine access, and update checks)
- Focus the vision self-test on the camera-view/jog path: `py -3 -c "import hybrid_vision as hv; hv.test_mirar()"`
- Syntax check the shipped Python modules: `py -3 -m py_compile hybrid_vision.py ruida.py ruidavision\app.py ruidavision\actualizar.py`
- Build the Windows executable and installer: `build_windows.bat` (runs the checks above, then requires PyInstaller and Inno Setup 6)

There is no configured linter or pytest/unittest discovery suite. The self-tests are executable module commands; they do not provide a named individual-assertion selector. Hardware workflows such as `calibrate` and `run` need the actual cameras/controller; use `run --no-move` to inspect detection without moving the head.

## Architecture

- `ruida.py` implements the controller protocol and its lower-level encoding. Manual jog and position use the 50207 panel channel; absolute LightBurn Move > Go travel was captured on 50200 as `D9 10 00 <X:5 bytes><Y:5 bytes>`. `Ruida.move_to_position` reproduces that observed command, checks `Panel.SAFE`, and `move_and_wait` confirms arrival through 50207. The native travel has no app-level cancel; do not claim the jog `Parar` interrupts it. `Panel.move_to` remains disabled for timed-key moves.
- `hybrid_vision.py` is the shared vision and workflow layer: camera acquisition, mark detection, pixel-to-machine-mm homography, calibration, coarse top-camera positioning, and head-camera fine centering. It also exposes CLI workflows (`test`, `scan`, `cams`, `calibrate`, `fov`, `run`). The GUI imports and reuses this module rather than maintaining a separate vision implementation.
- `ruidavision/app.py` is the Tk desktop client (Vivo, Calibrar, Marcas, Ajustes). Keep Tk widgets on the UI thread: camera capture runs in `Video`, machine operations are serialized through a one-worker executor, and worker-to-UI updates are queued and drained with `after`. Live rendering uses the latest frame rather than a backlog. Do not block the UI with sleeps or OpenCV windows; display images through the existing PIL/Tk path.
- `ruidavision/actualizar.py` checks GitHub releases and launches the Inno Setup installer. It intentionally uses only the standard library so the updater does not depend on the app's third-party packages.
- Windows packaging is wired through `RuidaVision.spec`, `build_windows.bat`, and `installer/RuidaVision.iss`. `ruidavision/__init__.py` is the sole version source; packaging reads it rather than maintaining another version number.

## Repository-specific conventions

- `calib.json` is machine-specific runtime state. The vision module uses it for camera indices/settings and the homography; calibration points must cover the usable bed because a homography extrapolates poorly outside the measured region. Avoid changing or overwriting the checked-in calibration while testing. Both the frozen app and source-launched GUI use `%LOCALAPPDATA%\Ruida Vision` when its `calib.json` exists; source mode falls back to the repository data only when no user calibration exists.
- Preserve the separation between detection and motion. `run` filters candidates to the machine-safe area (or an explicit `--roi`); do not relax travel checks to compensate for bad detections or an out-of-coverage calibration. The software moves the head only; it does not fire the laser or move Z.
- Print and Cut moves one selected point per click with the captured native `D9 10` command, not simulated keys. Keep destinations inside 0–500 × 0–400 mm, require a valid starting position and confirmed arrival, and explain that travel cannot be canceled from the app. `Origen 0,0` is enabled only through the native move with arrival confirmation and fail-closed behavior; the calibration `Estacionar` and automatic two-point centering remain disabled. The native packet does not encode speed; do not invent a speed parameter. Simultaneous 50207 jog keys are not interpolation, and standalone 50200 `0x88`/`0x89` only ACK on this machine.
- Latest physical trial: the user confirmed that the printed/detected coordinates and green Mover labels use different stages of the `cam_offset_mm` conversion; the existing `_fin_punto` conversion is considered correct. The remaining task is to center the head-camera view on each circular mark and measure per-run residuals, which vary between detections. Track it in `coplitovs-notas.md`; do not change homography/offset or perform physical moves without verified measurements and user confirmation. The earlier max/mean homography residual was 0.309/0.172 mm. Never relax `Panel.SAFE`.
- LightBurn and this app use the fixed local 40200 source port for 50200 traffic; close LightBurn before native moves to avoid a bind conflict. Never infer that a timeout stopped a movement command.
- Keep app callbacks asynchronous and serialize controller access. The panel socket uses a fixed local port, so opening concurrent `Panel`/machine sessions can conflict; the GUI's `Maquina` owns the session and releases it where workflows require a fresh connection.
- Calibration records the pixel actually selected in the frozen overhead image together with the controller position read at that moment. The GUI's WASD direction mapping is derived from the stored homography when available; avoid replacing it with an assumed fixed camera orientation.
- Calibrar provides the same manual jog semantics as Vivo, a shared step-size display with 0.5 mm mouse-wheel increments, a camera reconnect action, and frozen-photo-only threshold/minimum-area tuning. Keep these detector controls local to Calibrar; do not change production Print and Cut settings or persist them implicitly. Frozen camera capture must reconnect the previous live streams on both success and failure.
- The GUI smoke test is deliberately offline: fake `Video` and update checks before scheduled callbacks run, and replace config writes for the full test. Preserve those safeguards so tests cannot open real hardware, contact GitHub, or alter `calib.json`.
