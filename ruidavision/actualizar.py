"""Actualizacion OTA: mira la ultima release de GitHub y ejecuta su instalador.

Solo biblioteca estandar (urllib, json, subprocess): un actualizador que
depende de pip, de la red de otra manera o de una libreria mas es un
actualizador que no arranca el dia que hace falta.

Cadena: GET api/repos/<repo>/releases/latest -> si el tag es mas nuevo que la
version instalada, descarga el .exe adjunto a %TEMP% y lo lanza. La sustitucion
la hace el propio instalador (Inno Setup), no este modulo.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

from ruidavision import REPO, VERSION

API = "https://api.github.com/repos/%s/releases/latest"
UA = "ruida-vision/%s" % VERSION
TIMEOUT = 10          # segundos de la consulta a la API
DESCARGA = 900        # el instalador son ~200 MB: 15 min de margen


def numeros(v):
    """'v1.10' -> (1, 10). Tupla de enteros: se compara con < y > de verdad."""
    return tuple(int(n) for n in re.findall(r"\d+", str(v)))


def hay_actualizacion(mia, tag):
    """True si la release `tag` es mas nueva que la version `mia` instalada.

    Compara por numeros, no por texto: '1.10' es mas nuevo que '1.9' y el
    texto dice lo contrario. Sin tag (release sin nombre, API caida) no hay
    actualizacion que ofrecer, nunca se propone 'actualizar' a la nada.
    """
    return bool(tag) and numeros(tag) > numeros(mia)


def _peticion(url):
    """La peticion con su User-Agent (GitHub responde 403 sin el).

    headers por palabra clave, siempre: el segundo posicional de Request es
    `data`, y ahi el dict se vuelve el cuerpo y urlopen revienta. El actualizador
    fallaba en silencio, sin red y sin avisar.
    """
    return urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/vnd.github+json"})


def _abrir(url, timeout=TIMEOUT):
    # timeout por palabra clave tambien: el segundo posicional de urlopen es
    # `data` y ahi el timeout se mandaba como cuerpo (y el error decia <int>).
    return urllib.request.urlopen(_peticion(url), timeout=timeout)


def release(repo=REPO):
    """La ultima release publicada, o {} si la API no responde."""
    try:
        with _abrir(API % repo) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:            # sin red, sin token, 404, JSON roto
        return {"error": str(e)}


def instalador(rel):
    """El adjunto que es el instalador. {} si la release no lo trae."""
    for a in rel.get("assets", []) or []:
        n = a.get("name", "")
        if n.lower().endswith(".exe"):
            return a
    return {}


def comprobar(mia=VERSION, repo=REPO):
    """(hay_actualizacion, mensaje, release). Pensado para el boton."""
    rel = release(repo)
    if rel.get("error"):
        return False, "no se pudo mirar en GitHub: %s" % rel["error"], {}
    tag = rel.get("tag_name") or ""
    if not hay_actualizacion(mia, tag):
        return False, "instalada la %s, y %s es lo ultimo" % (mia, tag or "?"), rel
    a = instalador(rel)
    if not a:
        return False, "la %s no trae instalador adjunto" % tag, rel
    mb = a.get("size", 0) / 1048576.0
    return True, "esta la %s (%.0f MB). Al instalar se cierra esta ventana" % (tag, mb), rel


def descargar(url, destino=None):
    """Descarga a %TEMP% y devuelve la ruta. tqdm-free: una barra cada 4 MB."""
    destino = destino or os.path.join(tempfile.gettempdir(), url.rsplit("/", 1)[-1])
    total = 0
    with _abrir(url, DESCARGA) as r, open(destino, "wb") as f:
        while True:
            trozo = r.read(1 << 20)
            if not trozo:
                break
            f.write(trozo)
            total += len(trozo)
            yield total
    yield None                      # None = descarga terminada


ARGUMENTOS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
              "/CLOSEAPPLICATIONS")


def _orden(ruta, esperar=4):
    """La linea que arranca el instalador: un cmd que espera y luego lo lanza.

    Va en su propia funcion para que el autocomprobado pueda mirar la orden sin
    ejecutar nada. La cadena se monta a mano y NO con list2cmdline: ese entrecomilla
    los redirectiones y el `&&`, y el cmd se lo tomaria todo por nombre de
    programa."""
    exe = '"%s"' % ruta if (" " in ruta or '"' in ruta) else ruta
    return 'cmd /c ping -n %d 127.0.0.1 >nul && %s %s' % (
        esperar, exe, " ".join(ARGUMENTOS))


def lanzar(ruta, esperar=4):
    """Ejecuta el instalador DESPUES de que la app se cierre.

    El sintoma reportado era "descarga, cierra la app y no ejecuta el
    instalador", y la causa es que se lanzaba con la app todavia viva: Inno
    Setup ve el ejecutable abierto, no puede sustituirlo y en modo silencioso
    no hay nadie a quien preguntarselo, asi que se va sin hacer nada y sin
    avisar. Con `/CLOSEAPPLICATIONS` de los argumentos, Inno la cierra el
    mismo, y el arranque se hace desde un `cmd` desligado que primero espera
    unos segundos a que esta ventana desaparezca de verdad.

    El `cmd` es la pieza que hace que sobreviva: un Popen que cuelga de esta
    app muere con ella, y lo que hacia falta era algo que siguiera vivo. Se
    lanza DETACHED y sin consola para que no aparezca una ventana negra."""
    if not os.path.exists(ruta):
        raise IOError("no esta el instalador en %s" % ruta)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) \
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.Popen(_orden(ruta, esperar), close_fds=True,
                            creationflags=flags)


def main(a):
    if a.cmd == "test":
        return test()
    if a.cmd == "mirar":
        hay, msg, _ = comprobar()
        print(("HAY" if hay else "NO HAY") + " actualizacion: " + msg)
        return 0
    hay, msg, rel = comprobar()
    print(msg)
    if not hay:
        return 0
    a_ = instalador(rel)
    for total in descargar(a_["browser_download_url"]):
        if total is None:
            break
        print("\rdescargado %.0f MB" % (total / 1048576.0), end="")
    print("\ndescargado %.0f MB" % (a_["size"] / 1048576.0))
    lanzar(os.path.join(tempfile.gettempdir(), a_["name"]))
    print("instalador lanzado; esta ventana se cierra sola")
    return 0


def test():
    """Lo unico que puede fallar aqui es la COMPARACION de versiones."""
    ok = 0

    def chk(desc, got, want):
        nonlocal ok
        if got == want:
            print("  OK  %s" % desc)
            ok += 1
        else:
            print("FALLO %s: dio %r y queria %r" % (desc, got, want))

    chk("numeros de v1.10", numeros("v1.10"), (1, 10))
    chk("1.9 es mas viejo que 1.10", hay_actualizacion("1.9", "v1.10"), True)
    chk("1.10 no es mas nuevo que 1.10", hay_actualizacion("1.10", "v1.10"), False)
    chk("2.0 es mas nuevo que 1.10", hay_actualizacion("1.10", "v2.0"), True)
    chk("1.2 es mas viejo que 1.1", hay_actualizacion("1.2", "v1.1"), False)
    chk("sin tag no hay actualizacion", hay_actualizacion("1.0", ""), False)
    chk("release sin adjuntos", instalador({"assets": []}), {})
    chk("busca el .exe", instalador({"assets": [
        {"name": "notas.txt"}, {"name": "instalar_RuidaVision_1.1.exe"}]}
    ).get("name"), "instalar_RuidaVision_1.1.exe")
    # La peticion se construye bien o no hay actualizacion que llegue: sin
    # User-Agent GitHub da 403 y con `data` de mas urlopen ni responde.
    p = _peticion("https://api.github.com/repos/x/y/releases/latest")
    chk("la peticion no lleva cuerpo", p.data, None)
    chk("la peticion lleva User-Agent", bool(p.get_header("User-agent")), True)
    chk("la peticion pide JSON de GitHub",
        p.get_header("Accept"), "application/vnd.github+json")
    # lanzar(): el sintoma era "cierra la app y no ejecuta el instalador", y
    # venía de lanzarlo con la app viva. Se comprueba la orden, no el proceso.
    ruta_ok = os.path.join(tempfile.gettempdir(), "instalador_de_prueba.exe")
    open(ruta_ok, "wb").close()
    try:
        lanzar("/no/existe/instalador.exe")
        chk("lanzar avisa si el instalador no esta", "no lanzo", "IOError")
    except IOError as e:
        chk("lanzar avisa si el instalador no esta", os.path.basename(str(e)),
            "instalador.exe")
    chk("el arranque espera a que la app se cierre",
        "ping -n 4 127.0.0.1 >nul &&" in _orden(ruta_ok), True)
    chk("el instalador va con /CLOSEAPPLICATIONS",
        all(a in _orden(ruta_ok) for a in ARGUMENTOS), True)
    print("actualizador: %d comprobaciones OK" % ok)
    return 0 if ok == 14 else 1


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("test", help="autocomprobado del comparador de versiones")
    sub.add_parser("mirar", help="solo mirar si hay actualizacion")
    sub.add_parser("instalar", help="descargar el instalador y lanzarlo")
    sys.exit(main(p.parse_args()))
