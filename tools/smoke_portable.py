"""Exercise a fresh extracted Windows ZIP using only temporary synthetic data."""
import argparse
import base64
import io
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener
from zipfile import ZipFile

import pefile
from PIL import Image

BASE = "http://127.0.0.1:8766/"
client = build_opener(ProxyHandler({}))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def get(path):
    with client.open(BASE + path, timeout=3) as response:
        return response.read()


def post(path, token, data=None):
    req = Request(BASE + path, method="POST", data=json.dumps(data).encode(),
                  headers={"X-JMangaShelf":"1", "X-JMangaShelf-Instance":token,
                           "Content-Type":"application/json"})
    with client.open(req, timeout=5) as response:
        return response.read()


def verify_pe(exe):
    with pefile.PE(str(exe)) as pe:
        require(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Subsystem == 2, "Se esperaba un EXE gráfico x64.")
        resources = {entry.id:entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        require(14 in resources and len(resources[3].directory.entries) == 7, "Faltan tamaños del icono incrustado.")
        require(pe.VS_FIXEDFILEINFO[0].ProductVersionMS == 65536, "Versión incorrecta.")


def wait_ready(directory, proc):
    identity = hashlib.sha256(os.fsencode(os.path.normcase(str(directory)))).hexdigest()
    for _ in range(100):
        require(proc.poll() is None, "El proceso terminó antes del arranque.")
        try:
            body = get("api/portable/instance")
        except (OSError, URLError):
            time.sleep(0.15)
            continue
        require(json.loads(body) == {"instance":identity}, "El servidor no pertenece a esta prueba.")
        html = get("")
        require(b"Cerrar JMangaShelf" in html and b"v1.0.0" in html, "Falta la interfaz portable.")
        return re.search(rb'name="jmangashelf-instance" content="([a-f0-9]+)"', html)[1].decode()
    raise RuntimeError("El portable no arrancó a tiempo.")


def smoke(archive, temp, chrome):
    work = temp / "directorio de trabajo"
    work.mkdir()
    # Strip project Python, site packages and PATH; Chrome uses a disposable profile.
    env = {k:v for k,v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV", "_PYI"))}
    env.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"), TEMP=str(work), TMP=str(work))
    with ZipFile(archive) as zipped:
        for name in zipped.namelist():
            path = Path(name)
            require(not path.is_absolute() and ".." not in path.parts and path.parts[0] == "JMangaShelf", "Ruta ZIP inesperada.")
            require(not any(p.lower() in {"data", "backups", "jmangashelf-datos", "anilist-covers", ".git", ".venv"} for p in path.parts)
                    and not re.search(r"\.(sqlite3?|db|log|gz|whl)(-|$)", name, re.I), "Archivo privado o generado en el ZIP.")
        zipped.extractall(temp / "primera")
        zipped.extractall(temp / "actualizada")
    previous = None
    expected_id = None
    old_token = None
    for phase in ("primera", "reinicio", "actualizada"):
        directory = temp / ("primera" if phase == "reinicio" else phase) / "JMangaShelf"
        if phase == "actualizada":
            shutil.copytree(previous / "JMangaShelf-datos", directory / "JMangaShelf-datos")
        exe = directory / "JMangaShelf.exe"
        verify_pe(exe)
        screenshot = work / (phase + ".png")
        profile = work / ("perfil-" + phase)
        env["BROWSER"] = (f'"{chrome}" --headless --disable-gpu --no-first-run --no-default-browser-check '
                          f'--disable-background-networking --disable-extensions --disable-sync --no-proxy-server '
                          f'"--user-data-dir={profile}" "--screenshot={screenshot}" '
                          '"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" %s')
        flags = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen([exe], cwd=work, env=env, creationflags=flags)
        try:
            token = wait_ready(directory, proc)
            for _ in range(120):
                if screenshot.exists() and screenshot.stat().st_size > 1000:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Chrome no produjo la captura del arranque automático.")
            works = json.loads(get("api/works"))
            if phase == "primera":
                require(works == [], "La instalación debe empezar vacía.")
                second = subprocess.run([exe], cwd=work, env=env, creationflags=flags, timeout=30)
                require(second.returncode == 0 and proc.poll() is None, "Falló el segundo arranque.")
                require(wait_ready(directory, proc) == token, "El segundo arranque cambió la instancia.")
                payload = {"title":"Ficha temporal del portable", "type":"manga", "status":"reading",
                           "chapter":"12", "rating":7, "links":[], "notes":"", "tags":[]}
                image = io.BytesIO()
                Image.new("RGB", (12, 16), "#ae84da").save(image, "PNG")
                payload["cover"] = {"kind":"local", "data":base64.b64encode(image.getvalue()).decode()}
                expected_id = json.loads(post("api/works", token, payload))["id"]
            else:
                require(len(works) == 1 and (works[0]["id"], works[0]["chapter"], works[0]["rating"]) ==
                        (expected_id, "12", 7), "Falló la persistencia.")
                try:
                    post("api/portable/shutdown", old_token)
                except HTTPError as error:
                    require(error.code == 409, "Respuesta inesperada para una pestaña antigua.")
                    error.close()
                else:
                    raise RuntimeError("Una pestaña antigua pudo cerrar la aplicación.")
            with Image.open(io.BytesIO(get(f"api/works/{expected_id}/cover"))) as cover:
                require(cover.format == "WEBP" and cover.size == (12, 16), "Falló la portada local en el EXE.")
            backup = json.loads(get("api/backup"))["works"]
            require(backup[0]["id"] == expected_id and backup[0]["cover"]["data"], "Falló la copia JSON del EXE.")
            post("api/portable/shutdown", token)
            require(proc.wait(timeout=15) == 0, "El cierre falló.")
            previous = directory
            old_token = token
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)
        print(phase + ": OK", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--chrome", type=Path, default=Path(os.environ["ProgramFiles"]) / "Google/Chrome/Application/chrome.exe")
    args = parser.parse_args()
    require(args.chrome.is_file(), "Hace falta Chrome para la comprobación del navegador.")
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", 8766))
    with tempfile.TemporaryDirectory() as temp:
        smoke(args.archive.resolve(), Path(temp).resolve(), args.chrome.resolve())
