"""Enlace UDP con controladora Ruida: movimiento (puerto 50200) y posicion en
tiempo real (puerto 50207).

Unidades de la maquina: milimetros. En el cable: micrómetros en enteros de 7
bits por byte, MSB primero (2 bytes relativo con signo, 5 bytes absoluto).

Ejecuta `python ruida.py test` para el autocomprobado (sin red, sin hardware).
"""

import argparse
import json
import math
import os
import socket
import sys
import threading
import time

MAGIC = 0x88
PORT_MOVE, SRC_MOVE = 50200, 40200
PORT_PANEL, SRC_PANEL = 50207, 40207

ACK = (0xC6, 0xCC)
ERR = (0x46, 0xCF)
RELTOL = 8.191
RELBASE = 16.384


# ---------------------------------------------------------------- codificacion

def _enc_um(n, nb):
    """Entero de micrómetros -> nb bytes de 7 bits (MSB primero)."""
    if n < 0 or n >= 128 ** nb:
        raise ValueError("%d µm no cabe en %d bytes" % (n, nb))
    out = bytearray(nb)
    for i in range(nb - 1, -1, -1):
        out[i] = n & 0x7F
        n >>= 7
    return bytes(out)


def enc(mm, nb):
    """mm -> nb bytes de 7 bits (micrómetros, MSB primero)."""
    return _enc_um(int(round(mm * 1000)), nb)


def dec(b):
    """nb bytes de 7 bits -> mm."""
    return _dec_um(b) / 1000.0


def _dec_um(b):
    n = 0
    for x in b:
        n = (n << 7) | (x & 0x7F)
    return n


def enc_rel(dx, dy):
    """Jog relativo con signo -> 4 bytes (2 por eje), rango ±8.191 mm."""
    out = b""
    for d in (dx, dy):
        n = int(round(d * 1000))
        if not -8191 <= n <= 8191:
            raise ValueError("jog %g mm fuera de ±8.191 mm: usa move_abs" % d)
        out += _enc_um(n + int(RELBASE * 1000) if n < 0 else n, 2)
    return out


def dec_rel(b):
    n = []
    for i in (0, 2):
        v = _dec_um(b[i:i + 2])
        n.append((v - int(RELBASE * 1000) if v > 8191 else v) / 1000.0)
    return tuple(n)


def swz(b, magic=MAGIC):
    out = bytearray()
    for x in b:
        fb, lb = x & 0x80, x & 1
        r = x - fb - lb
        r |= lb << 7
        r |= fb >> 7
        out.append(((r ^ magic) + 1) & 0xFF)
    return bytes(out)


def unswz(b, magic=MAGIC):
    out = bytearray()
    for x in b:
        r = (x - 1) ^ magic
        fb, lb = r & 0x80, r & 1
        out.append(((r - fb - lb) | (lb << 7) | (fb >> 7)) & 0xFF)
    return bytes(out)


# ------------------------------------------------------------------- movimiento

class Ruida:
    """Canal 50200: comandos de movimiento del formato de fichero .rd."""

    def __init__(self, ip, magic=MAGIC, src=SRC_MOVE, timeout=1.5, retries=3,
                 verbose=True):
        self.ip, self.magic, self.retries, self.verbose = ip, magic, retries, verbose
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("0.0.0.0", src))
        self.s.connect((ip, PORT_MOVE))
        self.s.settimeout(timeout)

    def _tx(self, payload, expect_ack=True):
        data = swz(payload, self.magic)
        cs = sum(data) & 0xFFFF
        pkt = bytes([cs >> 8, cs & 0xFF]) + data
        for attempt in range(self.retries + 1):
            self.s.send(pkt)
            try:
                r = self.s.recv(16)
            except socket.timeout:
                r = b""
            # siempre a la vista: sin esto no se puede depurar un ACK que
            # llega pero no mueve, que es justo lo que hacia la Ruida
            if self.verbose:
                print("[tx] %s -> %s" % (pkt.hex(" "),
                                        r.hex(" ") if r else "sin respuesta"))
            if r and r[0] in ACK:
                return
            if r and r[0] not in ERR and r:
                if self.verbose:
                    print("[ruida] respuesta inesperada %s (no ACK)" % r.hex())
                return
            if self.verbose:
                print("[ruida] sin ACK, reintento %d/%d %s"
                      % (attempt + 1, self.retries, r.hex() if r else "timeout"))
        raise IOError("la controladora no confirma el comando %s" % payload.hex())

    def move_abs(self, x, y):
        """Viaje con láser apagado a (x, y) mm absolutos."""
        self._tx(b"\x88" + enc(x, 5) + enc(y, 5))

    def set_param(self, param, value):
        """Escribe un parametro de la controladora (paquete `e7`).

        Es el mismo mecanismo que usa la cabecera de un `.rd`: aqui es donde
        de verdad se ajusta la velocidad de jog, que es la palanca del
        centrado fino. Los ids vienen del .lbset de LightBurn."""
        self._tx(b"\xe7" + _enc_um(int(param), 2) + _enc_um(int(value), 2))

    def jog(self, dx, dy):
        """Viaje relativo (láser apagado), ±8.191 mm por eje."""
        self._tx(b"\x89" + enc_rel(dx, dy))

    def stop(self):
        self._tx(b"\xe7\x00")

    def close(self):
        self.s.close()


# -------------------------------------------------------------------- posicion

def _xy(frame):
    """Los 10 bytes del informe a5 68 -> (x, y) en mm."""
    return (dec(frame[:5]), dec(frame[5:10])) if frame else None


class Panel:
    """Canal 50207: handshake 0xCC e informe a5 68 <X><Y>.

    Verificado en esta 7132G: el informe es la posicion del cabezal, y
    coincide con lo que muestra LightBurn. Solo llega como respuesta a un
    0xCC, nunca por su cuenta.
    """

    def __init__(self, ip, src=SRC_PANEL, verbose=True):
        self.verbose = verbose
        self._buf = b""
        self._beat = 0.0
        self._pending = None
        self._stop_event = threading.Event()
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("0.0.0.0", src))
        self.s.connect((ip, PORT_PANEL))
        self.s.settimeout(0.2)
        self.release()

    def release(self):
        # Suelta las cuatro teclas de jog. Si un proceso muere a
        # mitad de un pulso, el panel se queda con esa tecla pulsada y
        # despues ignora las nuevas hasta que llegue su keyup.
        for k in self.JOG.values():
            self.s.sendall(bytes([0xA5, 0x51, k]))

    def stop(self):
        """Cancela el movimiento activo y suelta las teclas sin esperar al pool."""
        self._stop_event.set()
        self.release()

    def resume(self):
        """Permite una nueva orden despues de un stop explicito."""
        self._stop_event.clear()

    def _drain(self):
        """Tira lo que hubiera pendiente en el socket.

        Cualquier informe que este aqui es ANTERIOR a la pregunta que viene,
        asi que no sirve para fechar la posicion: devolver el ultimo del buffer
        es leer el sitio donde estaba el cabezal hace un segundo. Vaciar antes
        de preguntar convierte el canal en un request/response de verdad."""
        self.s.settimeout(0.0)
        try:
            while self.s.recv(2048):
                pass
        except OSError:
            pass
        self._buf = b""
        self.s.settimeout(0.05)

    def _frame(self, timeout):
        """Un informe a5 68 de los que lleguen DESPUES de la pregunta."""
        end = time.time() + timeout
        while True:
            i = self._buf.find(b"\xa5\x68")
            if i >= 0 and len(self._buf) >= i + 12:
                frame = self._buf[i + 2:i + 12]   # los 10 bytes tras a5 68
                self._buf = self._buf[i + 12:]
                return frame
            now = time.time()
            if now > end:
                return None
            if now - self._beat > 1.0:
                self._beat = now
                try:
                    self.s.send(b"\xce")
                except OSError:
                    return None
            try:
                chunk = self.s.recv(2048)
                if chunk:
                    self._buf += chunk
            except socket.timeout:
                pass
            except OSError:
                return None

    def handshake(self, timeout=1.0):
        """Envia 0xCC. True si la controladora contesta."""
        self._drain()
        self.s.send(b"\xcc")
        self._beat = time.time()
        self._pending = _xy(self._frame(timeout))
        return self._pending is not None

    def position(self, timeout=3.0):
        """(x, y) en mm del informe que contesta a este 0xCC.

        La controladora solo manda a5 68 como respuesta a un 0xCC, nunca por
        su cuenta, asi que se vacia el socket, se pregunta y se lee solo lo
        que venga de verdad."""
        if self._pending:
            p, self._pending = self._pending, None
            return p
        self._drain()
        self.s.send(b"\xcc")
        self._beat = time.time()
        return _xy(self._frame(timeout))

    # ------------------------------------------------------------------- jog
    # El 50200 (subida de trabajos) confirma el paquete pero no mueve: el
    # movimiento en vivo va por las teclas del teclado de este canal. Medido
    # en esta maquina: pulso t ms -> ~0.19 mm de tiempo muerto + 0.036 mm/ms.
    # El termino fijo hace que el minimo sea ~0.22 mm, pero es DETERMINISTA
    # (dos pulsos de 1 ms dan los mismos 0.219 mm), asi que un bucle cerrado
    # converge a esa rejilla.
    JOG = {"+X": 0x01, "-X": 0x02, "-Y": 0x03, "+Y": 0x04}
    # Medido en esta maquina (1..100 ms), ver README:
    #   1 ms -> 0.219 mm   10 ms -> 0.498   50 ms -> 2.264   100 ms -> 6.150
    # o sea ~0.19 mm de tiempo muerto + 0.066 mm/ms. Con pulsos largos la
    # velocidad sigue subiendo (2 s recorren los 500 mm del tope), asi que un
    # pulso NUNCA pasa de 100 ms: 6.15 mm es el mayor salto que se prescribe con
    # el modelo. Lo que falte se hace con varios pulsos, cada uno releido.
    # ponytail: si bajas la velocidad de jog en el panel de la Ruida, vuelve a
    # medir con _pulse.py y ajusta estos tres numeros; el tiempo muerto baja
    # con la velocidad y con el la resolucion del centrado fino.
    # El suelo de ~0.18 mm NO baja con la velocidad de jog: es un desplazamiento
    # fijo, no una latencia (bajar la velocidad 3x lo dejo igual). Se paga una
    # vez por pulso, asi que el minimo absoluto de posicionamiento es ~0.2 mm
    # mientras la maquina tenga este backlash. Se compensa restandolo, que es
    # justo lo que hace move_to.
    JOG_STEP = 0.18     # mm de desplazamiento fijo por pulso
    # Con el perfil "jog lento" (5 mm/s, 800 mm/s2) X e Y ya casi coinciden:
    # 0.031 mm/ms los dos. Antes eran 0.092 y 0.045 con el perfil de fabrica.
    JOG_RATE = 0.031    # mm por ms
    # Tope de 100 ms por pulso (~3.4 mm). La curva es una rampa, no una recta:
    # el modelo lineal se queda CORTO en pulsos cortos, que es el lado bueno
    # de equivocarse, porque nunca sobrepasa lo previsto. Y el tope de 100 ms
    # impide que un pulso se vaya al tope de recorrido.
    JOG_MAX_MS = 100.0
    MOVE_COARSE_MM = 8.0
    MOVE_BRAKE_MM = 4.0
    MOVE_MAX_SEGMENT_MS = 400.0
    MOVE_SAFE_MARGIN_MM = 15.0
    # Caja de viaje segura. Medida en esta maquina: el tope del recorrido esta
    # en 500.049 x 400.046. ponytail:Measure con el cabezal en las cuatro
    # esquinas y ajusta; si el soft limit real es menor, baja estos numeros.
    SAFE = (0.0, 500.0, 0.0, 400.0)

    def _ok(self, p):
        """Una lectura creible: dentro de la caja. Fuera de ella el informe
        esta descolocado (buffer mal cortado) y no se puede pilotar con el."""
        x0, x1, y0, y1 = self.SAFE
        return p is not None and x0 - 1 <= p[0] <= x1 + 1 and y0 - 1 <= p[1] <= y1 + 1

    def hold(self, key, ms):
        """Mantiene una tecla de jog `ms` milisegundos y la suelta."""
        self.s.sendall(bytes([0xA5, 0x50, self.JOG[key]]))
        time.sleep(ms / 1000.0)
        self.s.sendall(bytes([0xA5, 0x51, self.JOG[key]]))

    def hold_vector(self, x_key, x_ms, y_key, y_ms):
        """Ejecuta un tramo XY temporizado, liberando cada eje al vencer."""
        if self._stop_event.is_set():
            raise InterruptedError("movimiento detenido por el usuario")
        active = []
        started = time.monotonic()
        try:
            for key, duration in ((x_key, x_ms), (y_key, y_ms)):
                if key is None or duration <= 0:
                    continue
                code = self.JOG[key]
                self.s.sendall(bytes([0xA5, 0x50, code]))
                active.append((key, code, started + duration / 1000.0))
            for key, code, deadline in sorted(active, key=lambda item: item[2]):
                if self._stop_event.wait(max(0.0, deadline - time.monotonic())):
                    raise InterruptedError("movimiento detenido por el usuario")
                self.s.sendall(bytes([0xA5, 0x51, code]))
                active = [item for item in active if item[1] != code]
        finally:
            for _, code, _ in active:
                self.s.sendall(bytes([0xA5, 0x51, code]))

    def jog_hold(self, key, parar, margen=10.0, poll=0.08, max_ms=120000.0,
                 verbose=True, corte=None):
        """Mantiene la tecla pulsada hasta que `parar` (threading.Event) se
        active.

        UN keydown para todo el recorrido, no una ristra de pulsos: la
        controladora acelera al pulsar y frena al soltar, asi que encadenar
        pulsos paga una rampa por cada uno y aun asi da tirones. El lazo solo
        mira la posicion para no acercarse al tope de viaje; la tecla se suelta
        SIEMPRE (finally), tambien si el proceso muere a mitad.

        El tope de `max_ms` es un backstop por si el informe de posicion se
        queda pegado: a 5 mm/s no llega a cortar en una cama entera.

        `corte(p)` es opcional y es lo que hace el movimiento grande: se llama
        con cada posicion y, cuando devuelve True, suelta la tecla. Sin el,
        el lazo no sabe mas que el tope de viaje, y para ir a un sitio habria
        que adivinar cuando frenar.
        """
        k = self.JOG[key]
        eje, sube = key[1], key[0] == "+"
        tope = (self.SAFE[1] if sube else self.SAFE[0]) if eje == "X" \
            else (self.SAFE[3] if sube else self.SAFE[2])
        t0, malas, p = time.time(), 0, None
        self.s.sendall(bytes([0xA5, 0x50, k]))
        try:
            while not parar.is_set() and not self._stop_event.is_set():
                if (time.time() - t0) * 1000.0 >= max_ms:
                    if verbose:
                        print("[jog] %s cortado por tiempo" % key)
                    break
                time.sleep(poll)
                p = self.position(0.5)
                if not self._ok(p):
                    malas += 1
                    if malas >= 2:
                        if verbose:
                            print("[jog] %s cortado: sin posicion creible" % key)
                        break
                    continue
                malas = 0
                cur = p[0] if eje == "X" else p[1]
                if (tope - cur if sube else cur - tope) <= margen:
                    if verbose:
                        print("[jog] %s cortado a %.3f mm del tope" % (key, cur))
                    break
                if corte is not None and corte(p):
                    break                 # destino alcanzado: no hace falta parar
        finally:
            self.s.sendall(bytes([0xA5, 0x51, k]))
        return p

    def settled(self, tries=3, tol=0.005, pause=0.08):
        """Espera a que la posicion se pare: a alta velocidad el informe va
        retrasada ~9 ms y leer en caliente da saltos de varios mm. Rechaza
        lecturas fuera de la caja de viaje."""
        prev = None
        for _ in range(tries):
            time.sleep(pause)
            p = self.position(2.0)
            if not self._ok(p):
                continue
            if prev and abs(p[0] - prev[0]) < tol and abs(p[1] - prev[1]) < tol:
                return p
            prev = p
        return prev

    def _ir_hacia(self, key, destino, margen=4.0, margen_tope=10.0, poll=0.04,
                  desde=None, max_ms=120000.0):
        """Jog continuo en `key` hasta la zona de frenado del destino.

        El corte es direccional: si una lectura llega tarde y ya cruzo el
        destino, tambien suelta la tecla. Comparar solo la distancia absoluta
        dejaba el jog activo tras un sobrepaso y podia llevarlo hasta el tope.
        """
        axis = 0 if key[1] == "X" else 1
        if desde is None:
            desde = self.position(2.0)
        if not self._ok(desde):
            return None
        inicio = desde[axis]
        sentido = 1 if key[0] == "+" else -1
        if (destino - inicio) * sentido <= 0:
            raise ValueError("la direccion %s no lleva al destino %.3f"
                             % (key, destino))
        if abs(destino - inicio) <= margen:
            return desde
        sentido_incorrecto = threading.Event()

        def cortar(p):
            actual = p[axis]
            if (actual - inicio) * sentido < -0.5:
                sentido_incorrecto.set()
                return True
            return (destino - actual) * sentido <= margen

        if self.verbose:
            print("[jog] %s: %.3f -> %.3f mm (destino %.3f)"
                  % (key, inicio, destino, destino))
        p = self.jog_hold(
            key, threading.Event(), margen=margen_tope, poll=poll,
            max_ms=max_ms, verbose=False, corte=cortar)
        if self.verbose:
            print("[jog] %s: lectura final %s" % (key, p))
        if sentido_incorrecto.is_set():
            raise RuntimeError(
                "el jog %s movio el eje en sentido contrario; se cancelo antes "
                "del limite (inicio %.3f, lectura %s)"
                % (key, inicio, p))
        return p if self._ok(p) else None

    def move_to(self, x, y, tol=0.1, tries=250, budget=120.0, verbose=True):
        """Lleva el cabezal a (x, y) mm.

        Los tramos largos coordinan ambos ejes en una diagonal temporizada.
        Cada segmento tiene una duracion maxima, tras la cual se relee la
        posicion; la duracion por eje conserva la proporcion hacia el destino.
        Los pulsos se reservan para el ajuste final. Se evita dejar un jog
        continuo esperando una lectura de posicion que puede llegar tarde.
        """
        x0, x1, y0, y1 = self.SAFE
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            raise ValueError("destino %.3f, %.3f fuera de la caja %s"
                             % (x, y, self.SAFE))
        self.resume()
        t0 = time.time()
        p = self.position(2.0)
        if not self._ok(p):
            raise RuntimeError("sin lectura valida de la posicion inicial")

        rates = [self.JOG_RATE, self.JOG_RATE]
        for _ in range(tries):
            if self._stop_event.is_set():
                raise InterruptedError("movimiento detenido por el usuario")
            if time.time() - t0 > budget:
                break
            dx, dy = x - p[0], y - p[1]
            if max(abs(dx), abs(dy)) <= self.MOVE_COARSE_MM:
                break
            deltas = [dx, dy]
            active_axes = [abs(delta) > self.MOVE_COARSE_MM
                           for delta in deltas]
            bounds = (x1, y1)
            safe_dists = []
            for i, delta in enumerate(deltas):
                if delta > 0:
                    safe_dists.append(max(
                        0.0, bounds[i] - p[i] - self.MOVE_SAFE_MARGIN_MM))
                else:
                    safe_dists.append(max(
                        0.0, p[i] - (x0, y0)[i] - self.MOVE_SAFE_MARGIN_MM))
            progress = [
                min(1.0, max(0.0, 1.0 - self.MOVE_BRAKE_MM / abs(deltas[i])),
                    safe_dists[i] / abs(deltas[i]))
                for i in range(2) if active_axes[i]
            ]
            fraction = min(progress, default=0.0)
            raw_ms = [
                abs(deltas[i]) * fraction / rates[i] if active_axes[i] else 0.0
                for i in range(2)
            ]
            longest = max(raw_ms)
            if longest <= 0:
                break
            scale = min(1.0, self.MOVE_MAX_SEGMENT_MS / longest)
            durations = [duration * scale for duration in raw_ms]
            # Mantener una direccion diagonal recta requiere que ambas teclas
            # empiecen juntas y cada una se suelte cuando complete su proporcion.
            keys = [
                ("+" if dx > 0 else "-") + "X" if durations[0] > 0 else None,
                ("+" if dy > 0 else "-") + "Y" if durations[1] > 0 else None,
            ]
            if verbose:
                print("[jog] diagonal: %.3f, %.3f -> %.3f, %.3f; "
                      "tramo %.0f/%.0f ms"
                      % (p[0], p[1], x, y, durations[0], durations[1]))
            self.hold_vector(keys[0], durations[0], keys[1], durations[1])
            if self._stop_event.is_set():
                raise InterruptedError("movimiento detenido por el usuario")
            actual = self.position(2.0)
            if not self._ok(actual):
                raise RuntimeError("sin lectura valida despues del tramo diagonal")
            moved = [actual[0] - p[0], actual[1] - p[1]]
            requested = [dx, dy]
            for axis, delta, direction in zip(("X", "Y"), moved, requested):
                if abs(direction) > self.MOVE_COARSE_MM and delta * direction < -0.05:
                    raise RuntimeError(
                        "el tramo diagonal movio %s en sentido contrario "
                        "(%.3f mm); movimiento cancelado" % (axis, delta))
            for i, duration in enumerate(durations):
                if duration > 0:
                    measured = abs(moved[i]) / duration
                    if measured > 0.001:
                        rates[i] = min(0.15, max(0.005, measured))
            if verbose:
                print("[jog] lectura tras diagonal: %.3f, %.3f "
                      "(cambio %+.3f, %+.3f)"
                      % (actual[0], actual[1], moved[0], moved[1]))
            p = actual

        for i in range(tries):
            if self._stop_event.is_set():
                raise InterruptedError("movimiento detenido por el usuario")
            if time.time() - t0 > budget:
                break
            if not self._ok(p):
                if verbose:
                    print("[jog] lectura no creible, reintento")
                p = self.position(2.0)
                continue
            dx, dy = x - p[0], y - p[1]
            if max(abs(dx), abs(dy)) <= tol:
                return p
            if verbose:
                print("[jog] destino %.3f, %.3f; posicion %.3f, %.3f; "
                      "diferencia %.3f, %.3f"
                      % (x, y, p[0], p[1], dx, dy))
            for axis, indice, destino in (("X", 0, x), ("Y", 1, y)):
                if self._stop_event.is_set():
                    raise InterruptedError("movimiento detenido por el usuario")
                d = destino - p[indice]
                if abs(d) <= tol:
                    continue
                ms = (abs(d) - self.JOG_STEP) / self.JOG_RATE
                ms = min(self.JOG_MAX_MS, max(1.0, ms))
                # no acercarse al tope de viaje desde fuera
                cur = p[indice]
                if verbose:
                    print("[jog] pulso %s desde %.3f por %.1f ms"
                          % (axis, cur, ms))
                room = (x1 - cur) if d > 0 else (cur - x0)
                max_safe_ms = (room - 1.5 - self.JOG_STEP) / self.JOG_RATE
                if max_safe_ms < 1.0:
                    raise RuntimeError(
                        "no hay margen seguro para el pulso de %s; "
                        "posicion %.3f, limite %.3f" % (axis, cur,
                        x1 if axis == "X" and d > 0 else
                        x0 if axis == "X" else y1 if d > 0 else y0))
                ms = min(ms, max_safe_ms)
                self.hold(("+" if d > 0 else "-") + axis, ms)
                if self._stop_event.is_set():
                    raise InterruptedError("movimiento detenido por el usuario")
                actual = self.position(2.0)
                if not self._ok(actual):
                    raise RuntimeError(
                        "sin lectura valida despues del pulso %s; movimiento "
                        "cancelado" % axis)
                desplazamiento = actual[indice] - cur
                if desplazamiento * (1 if d > 0 else -1) < -0.05:
                    raise RuntimeError(
                        "el pulso de %s movio el eje en sentido contrario "
                        "(%.3f mm); movimiento cancelado"
                        % (axis, desplazamiento))
                p = actual
                if verbose:
                    print("[jog] pulso %s: lectura %.3f (cambio %+.3f mm)"
                          % (axis, actual[indice], desplazamiento))
            if verbose:
                print("[jog] %7.3f, %7.3f -> faltan %.3f, %.3f"
                      % (p[0], p[1], dx, dy))
        p = self.settled()
        if p and verbose and max(abs(x - p[0]), abs(y - p[1])) > tol:
            print("[jog] AVISO: se queda en %.3f, %.3f (pedidos %.3f, %.3f)"
                  % (p[0], p[1], x, y))
        return p

    def close(self):
        self.s.close()


def move_and_wait(pan, x, y, tol=0.1, timeout=120.0):
    """Lleva el cabezal a (x, y) mm y devuelve la posicion real.

    El movimiento va por el teclado del 50207, no por move_abs del 50200: en
    esta controladora los paquetes 0x88 sueltos se confirman con un c6 pero no
    mueven nada, solo ejecutan cuando forman parte de un trabajo .rd. El 50200
    se queda para analizar ficheros, no para mover el cabezal.

    Sin panel no hay quien mueva: se avisa y se devuelve None en vez de fingir
    un movimiento que no ocurrio."""
    if pan is None:
        print("AVISO: sin 50207 no puedo mover el cabezal")
        return None
    return pan.move_to(x, y, tol=tol, budget=timeout)


# ------------------------------------------------------------------ autocomprobado

def selftest():
    for b in range(256):
        assert unswz(swz(bytes([b])))[0] == b, "swizzle ida y vuelta en %02x" % b
    for dx, dy, want in ((8.191, 0, "3f7f"), (-8.191, 0, "4001"),
                         (4.0, 0, "1f20"), (-4.0, 0, "6060")):
        assert enc_rel(dx, dy)[:2].hex() == want, (dx, enc_rel(dx, dy).hex())
        assert dec_rel(enc_rel(dx, dy))[0] == dx, dx
    assert (b"\x89" + enc_rel(4.0, -4.0)).hex() == "891f206060"
    mv = b"\x88" + enc(281.424, 5) + enc(35.158, 5)
    assert mv.hex() == "8800001116500000021256", mv.hex()
    assert abs(dec(b"\x00\x00\x11\x16\x50") - 281.424) < 1e-6
    assert abs(dec(b"\x00\x00\x02\x12\x56") - 35.158) < 1e-6
    assert dec_rel(enc_rel(1.234, -5.678)) == (1.234, -5.678)
    for d in (0.0, 0.001, 1.234, 350.0, 900.123):
        assert dec(enc(d, 5)) == round(d, 3)
    p = bytes.fromhex("a56800003d046d0000244f6f")
    assert (dec(p[2:7]), dec(p[7:12])) == (1000.045, 600.047)
    assert _xy(p[2:12]) == (1000.045, 600.047)
    # regresion: el informe de posicion va pegado al 0xCC del handshake. Si se
    # tira, position() se queda sin nada que leer y el canal parece mudo.
    REPORT = bytes.fromhex("a56800003d046d0000244f6f")   # 1000.045, 600.047

    class FakeSock:
        """Como la controladora: un informe por 0CC recibido, y nada mas."""

        def __init__(self):
            self.sent = []
            self._due = False

        def bind(self, a):
            pass

        def connect(self, a):
            pass

        def settimeout(self, t):
            pass

        def send(self, d):
            self.sent.append(d)
            if d == b"\xcc":          # el 0xce es keepalive, no pregunta
                self._due = True

        sendall = send

        def recv(self, n):
            if not self._due:
                raise socket.timeout()
            self._due = False
            return REPORT

        def getsockname(self):
            return ("0.0.0.0", 0)

        def close(self):
            pass
    pn = Panel.__new__(Panel)
    pn.verbose, pn._buf, pn._beat, pn._pending = False, b"", 0.0, None
    pn._stop_event = threading.Event()
    pn.s = FakeSock()
    want = (1000.045, 600.047)
    assert pn.handshake(timeout=0.1) is True
    assert pn.position(timeout=0.1) == want, "el handshake se comio el informe"
    pn._pending = None
    assert pn.position(timeout=0.1) == want, "position() no pregunta con 0xCC"
    assert b"\xcc" in pn.s.sent, "no se pregunto al menos una vez"
    # jog continuo: una sola pulsacion de tecla, se suelta SIEMPRE, y corta
    # antes de llegar al tope de viaje. Sin esto, un evento que no llega deja
    # la tecla pegada y el cabezal contra el tope.
    class Nadie:
        def is_set(self):
            return False

    class Cuenta:
        """Deja pasar n vueltas de lazo y luego corta."""

        def __init__(self, n):
            self.n = n

        def is_set(self):
            self.n -= 1
            return self.n < 0

    pn.position = lambda t=0: (250.0, 200.0)
    pn.s.sent[:] = []
    pn.jog_hold("+X", Cuenta(2), poll=0.01, verbose=False)
    assert pn.s.sent[0].hex() == "a55001", pn.s.sent[0].hex()
    assert pn.s.sent[-1].hex() == "a55101", "el jog continuo no solto la tecla"
    assert pn.s.sent.count(bytes.fromhex("a55001")) == 1, "mas de un keydown"
    pn.position = lambda t=0: (250.0, 396.0)          # a 4 mm del tope de Y
    pn.s.sent[:] = []
    pn.jog_hold("+Y", Nadie(), poll=0.01, verbose=False)
    assert pn.s.sent[-1].hex() == "a55104", "ni corto ni solto en el tope"
    pn.s.sent[:] = []
    pn.hold_vector("+X", 2.0, "-Y", 4.0)
    assert pn.s.sent[:2] == [bytes.fromhex("a55001"), bytes.fromhex("a55003")]
    assert pn.s.sent.count(bytes.fromhex("a55101")) == 1
    assert pn.s.sent.count(bytes.fromhex("a55103")) == 1
    assert pn.s.sent[-1] == bytes.fromhex("a55103"), pn.s.sent
    # El jog manual corta al alcanzar destino y no solo por el tope.
    pos = [200.0]

    def avanza(t=0):
        pos[0] += 5.0                    # el cabezal responde al keydown
        return (pos[0], 200.0)
    pn.position = avanza
    corto = []
    p = pn.jog_hold("+X", Nadie(), poll=0.001, verbose=False,
                    corte=lambda q: (corto.append(q[0]), q[0] >= 205.0)[1])
    assert corto, "el corte por destino no se llego a mirar"
    assert 205.0 <= p[0] < 220.0, ("se paso del destino", p)
    assert pn.s.sent[-1].hex() == "a55101", "el corte por destino no solto la tecla"
    # Y el lazo entero: _ir_hacia tiene que devolver la posicion sin quedarse
    # colgado esperando al tope.
    pos[0] = 190.0
    p = pn._ir_hacia("+X", 205.0, margen=3.0, poll=0.001)
    assert p is not None and abs(p[0] - 205.0) <= 3.0, p
    # Una lectura tardia que cruza el destino tambien debe cortar el jog.
    pn.position = lambda t=0: (190.0, 200.0)
    posiciones_tardias = iter((200.0, 210.0, 300.0))

    def jog_con_lectura_tardia(key, parar, corte, **kw):
        for x_leido in posiciones_tardias:
            actual = (x_leido, 200.0)
            if corte(actual):
                return actual
        raise AssertionError("el jog no corto al cruzar el destino")

    pn.jog_hold = jog_con_lectura_tardia
    p = pn._ir_hacia("+X", 205.0, margen=3.0, desde=(190.0, 200.0))
    assert p == (210.0, 200.0), p
    # Los tramos largos avanzan en diagonal con tiempos proporcionales por eje,
    # releen posicion entre segmentos y reservan pulsos para el ajuste final.
    for inicio, destino in (((149.139, 0.034), (333.711, 123.838)),
                            ((500.0, 400.0), (333.711, 123.838))):
        sim_pos = list(inicio)
        pulsos = []
        diagonales = []

        def pos_simulada(timeout=0):
            return tuple(sim_pos)

        def settled_no_deberia_llamarse(*a, **kw):
            raise AssertionError("move_to repitio lecturas settled innecesarias")

        def pulso_simulado(key, ms):
            axis = 0 if key[1] == "X" else 1
            sign = 1 if key[0] == "+" else -1
            paso = Panel.JOG_STEP + ms * Panel.JOG_RATE
            sim_pos[axis] += sign * paso
            pulsos.append((key, ms, tuple(sim_pos)))

        def vector_simulado(x_key, x_ms, y_key, y_ms):
            diagonales.append((x_key, x_ms, y_key, y_ms))
            for axis, key, ms in ((0, x_key, x_ms), (1, y_key, y_ms)):
                if key and ms > 0:
                    sign = 1 if key[0] == "+" else -1
                    sim_pos[axis] += sign * ms * Panel.JOG_RATE
            assert 0.0 <= sim_pos[0] <= 500.0
            assert 0.0 <= sim_pos[1] <= 400.0

        pn.position = pos_simulada
        pn.settled = settled_no_deberia_llamarse
        pn.hold = pulso_simulado
        pn.hold_vector = vector_simulado
        alcanzada = pn.move_to(*destino, verbose=False)
        error = math.hypot(alcanzada[0] - destino[0],
                           alcanzada[1] - destino[1])
        assert error <= 0.5, (inicio, destino, alcanzada, error)
        assert diagonales, "los ejes no se movieron simultaneamente"
        assert all(max(x_ms, y_ms) <= Panel.MOVE_MAX_SEGMENT_MS + 1e-6
                   for _, x_ms, _, y_ms in diagonales), diagonales
        primer_x, primer_y = diagonales[0][0], diagonales[0][2]
        assert primer_x and primer_y, ("el primer tramo no fue diagonal",
                                       diagonales[0])
        ratio_esperado = abs(destino[0] - inicio[0]) / abs(destino[1] - inicio[1])
        ratio_tramo = diagonales[0][1] / diagonales[0][3]
        assert abs(ratio_tramo - ratio_esperado) < 0.001, (
            ratio_tramo, ratio_esperado, diagonales[0])
        assert len(pulsos) <= 6, ("demasiados pulsos para el ajuste", pulsos)
        assert pulsos and all(ms <= Panel.JOG_MAX_MS for _, ms, _ in pulsos)
        assert all(0.0 <= q[0] <= 500.0 and 0.0 <= q[1] <= 400.0
                   for _, _, q in pulsos), pulsos[-1]
    # Un jog con polaridad fisica invertida debe abortar cerca del inicio,
    # no seguir hasta el limite seguro del eje.
    wrong_pos = [200.0, 200.0]
    pn.position = lambda t=0: tuple(wrong_pos)

    def jog_invertido(key, parar, corte, **kw):
        sign = 1 if key[0] == "+" else -1
        wrong_pos[0] -= sign * 0.6
        actual = tuple(wrong_pos)
        if corte(actual):
            return actual
        wrong_pos[0] -= sign * 0.6
        actual = tuple(wrong_pos)
        corte(actual)
        return actual

    pn.jog_hold = jog_invertido
    try:
        pn._ir_hacia("+X", 230.0, desde=(200.0, 200.0))
        raise AssertionError("un jog invertido no se cancelo")
    except RuntimeError as e:
        assert "sentido contrario" in str(e), str(e)
    assert wrong_pos[0] >= 199.0, ("el jog invertido avanzo demasiado", wrong_pos)
    pn.position = lambda t=0: tuple(wrong_pos)

    def pulso_invertido(key, ms):
        wrong_pos[0] += -0.6 if key == "+X" else 0.6

    pn.hold = pulso_invertido
    pn.settled = lambda *a, **kw: tuple(wrong_pos)
    try:
        pn.move_to(205.0, 200.0, verbose=False)
        raise AssertionError("un pulso invertido no se cancelo")
    except RuntimeError as e:
        assert "sentido contrario" in str(e), str(e)
    assert wrong_pos[0] >= 198.0, ("el pulso invertido avanzo demasiado", wrong_pos)
    pn.s.sent[:] = []
    pn.stop()
    teclas = list(pn.JOG.values())
    assert pn._stop_event.is_set(), "stop no cancelo el movimiento"
    assert pn.s.sent == [bytes([0xA5, 0x51, k]) for k in teclas], pn.s.sent
    pn.resume()
    assert not pn._stop_event.is_set(), "resume no rearmo el panel"
    assert Panel.move_to.__defaults__[2] == 120.0
    assert move_and_wait.__defaults__[1] == 120.0
    stopped_pos = [200.0, 200.0]
    pn.position = lambda t=0: tuple(stopped_pos)

    def parar_durante_pulso(key, ms):
        stopped_pos[0] += 1.0
        pn.stop()
    pn.hold = parar_durante_pulso
    pn.settled = lambda *a, **kw: tuple(stopped_pos)

    try:
        pn.move_to(205.0, 200.0, verbose=False)
        raise AssertionError("move_to ignoro PARAR")
    except InterruptedError as e:
        assert "detenido" in str(e), str(e)
    assert stopped_pos == [201.0, 200.0], ("move_to siguio tras PARAR", stopped_pos)
    # el detector de magic tiene que encontrar un magic que no es el de defecto
    moves = b"".join(b"\x89" + enc_rel(dx, dy) for dx, dy in
                     ((4.0, 2.0), (-1.5, 3.0), (2.0, -2.0), (0.5, 0.5),
                      (6.0, 1.0), (-3.0, -1.0), (1.0, 1.0), (8.0, 0.0)))
    for real_magic in (0x38, 0x11, 0x88, 0xC8):
        blob = swz(b"\x00" * 64 + moves + b"\xa8" * 8 + b"\x00" * 64, real_magic)
        best = max(range(256),
                   key=lambda m: blob.translate(
                       bytes(unswz(bytes([i]), m)[0] for i in range(256))
                   ).count(0))
        assert best == real_magic, (hex(real_magic), hex(best))
    print("ruida.py: OK (coordenadas 7 bits, ±8.191 mm, informe a5 68, magic)")


# --------------------------------------------------------------------------- cli

def _confirm(prompt):
    return input("%s [s/N] " % prompt).strip().lower().startswith("s")


CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib.json")


def _default_ip():
    """La IP vive en calib.json para que ambos scripts usen la misma.

    Importa por seguridad mas que por comodidad: teclear --ip a mano en
    cada comando es pedir que un 'move' acabe en otra maquina."""
    try:
        with open(CFG) as f:
            return json.load(f).get("ip", "192.168.1.50")
    except (OSError, ValueError):
        return "192.168.1.50"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ip", default=_default_ip(), help="IP de la Ruida")
    ap.add_argument("--magic", type=lambda s: int(s, 0), default=MAGIC)
    ap.add_argument("--src", type=int, default=SRC_MOVE,
                    help="puerto origen (cambiar si LightBurn tiene el 40200)")
    ap.add_argument("-v", "--verbose", action="store_true", default=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("test", help="autocomprobado sin red")
    sub.add_parser("magic", help="deducir el swizzle de un .rd de RDWorks")
    sub.add_parser("ping", help="handshake e informe de posicion")
    for name in ("move", "jog"):
        p = sub.add_parser(name, help="%s X Y (mm)" % name)
        p.add_argument("x", type=float)
        p.add_argument("y", type=float)
        p.add_argument("-y", "--yes", action="store_true")
    sub.add_parser("sniff", help="volcar los paquetes crudos de 50207")

    a = ap.parse_args()
    if a.cmd == "test":
        return selftest()
    if a.cmd == "magic":
        path = input("ruta del .rd de RDWorks: ").strip()
        data = open(path, "rb").read()
        if len(data) < 64:
            print("el .rd tiene %d bytes: no parece un fichero de trabajo"
                  % len(data))
            return
        # unswz permuta los 256 bytes -> tabla de traduccion y listo
        # No se cuentan opcodes: swz(0x00, 0x88) == 0x89, o sea que el padding
        # se serializa justo como el opcode de viaje relativo y contaminaria
        # cualquier recuento (hay 7 magics que lo mapean a un opcode). En su
        # lugar, en un .rd real el byte mas frecuente es 0x00 (padding) y solo
        # el magic correcto lo devuelve a cero.
        scores = []
        for m in range(256):
            t = bytes(unswz(bytes([i]), m)[0] for i in range(256))
            u = data.translate(t)
            scores.append((u.count(0), m))
        scores.sort(reverse=True)
        print("bytes analizados: %d" % len(data))
        for n, m in scores[:5]:
            print("  magic 0x%02x -> %6d ceros (%4.1f%%)"
                  % (m, n, 100.0 * n / len(data)))
        best, second = scores[0], scores[1]
        if best[0] < 0.1 * len(data) or best[0] < second[0] * 3:
            print("\nno se distingue. Comprueba que el .rd sea de RDWorks y para")
            print("una 7132G, o que tenga viajes (un trabajo minimo puede no")
            print("tener ningun viaje absoluto).")
        elif best[1] == MAGIC:
            print("\nes el 0x88 de serie: no hace falta --magic")
        else:
            print("\nusa --magic 0x%02x" % best[1])
        return
    if a.cmd == "ping":
        pan = Panel(a.ip)
        ok = pan.handshake()
        print("handshake:", ok)
        got = []
        for i in range(5):
            p = pan.position(1.0)
            got.append(p)
            print("  posicion %d: %s" % (i + 1, p))
            time.sleep(0.3)
        pan.close()
        if not ok or not any(got):
            print("\nsin respuesta de %s. Comprueba, por este orden:" % a.ip)
            print("  1. la IP: es la que tiene el script. Se cambia en calib.json")
            print("  2. que el PC este en la misma red que la Ruida (ipconfig)")
            print("  3. que el firewall no bloquee el 50207 UDP")
        else:
            print("\nestas coordenadas tienen que coincidir con las que pone "
                  "LightBurn. Si no, el informe A5 68 no es la posicion del cabezal")
        return
    if a.cmd == "sniff":
        pan = Panel(a.ip)
        pan.handshake()
        print("paquetes de %s:40207 -> %s:50207" % (pan.s.getsockname(), a.ip))
        try:
            while True:
                d = pan.s.recv(2048)
                q = d[2:12] if d[:2] == b"\xa5\x68" else None
                extra = "  pos=%s" % (dec(q[:5]), dec(q[5:10])) if q else ""
                print("%s%s" % (d.hex(" "), extra))
        except KeyboardInterrupt:
            return
    if not a.yes and not _confirm("Mover a %.3f, %.3f mm" % (a.x, a.y)):
        return
    pan = Panel(a.ip, verbose=a.verbose)
    was = pan.handshake() and pan.position(2.0)
    try:
        if a.cmd == "move":
            pan.move_to(a.x, a.y, tol=0.3, budget=60.0)
        else:
            # el 50200 no mueve en esta controladora; el desplazamiento
            # relativo se hace encadenando pulsos de teclado
            if was is None:
                print("sin informe de posicion, no puedo calcular el jog")
                return
            pan.move_to(was[0] + a.x, was[1] + a.y, tol=0.3, budget=60.0)
        if was:
            time.sleep(1.0)
            now = pan.position(3.0)
            print("posicion: %s -> %s" % (was, now if now else "sin informe"))
    finally:
        pan.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
