"""Windowless entry point for the Windows onedir distribution."""
import ctypes
import hashlib
import json
import logging
import os
import secrets
import socket
import sys
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event, Thread
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from flask import abort, jsonify, request
from waitress import create_server, wasyncore

from app import create_app
from launcher import LocalOnly

HOST = "127.0.0.1"
PORT = 8766
URL = f"http://{HOST}:{PORT}/"


def notice(message):
    ctypes.windll.user32.MessageBoxW(None, message, "JMangaShelf", 0x40)


def open_library():
    try:
        if webbrowser.open(URL):
            return
    except (OSError, webbrowser.Error):
        pass
    notice(f"Abre {URL} en tu navegador para acceder a la biblioteca.")


def same_instance(identity):
    opener = build_opener(ProxyHandler({}), LocalOnly())
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            with opener.open(URL + "api/portable/instance", timeout=0.5) as response:
                return json.loads(response.read(256)) == {"instance": identity}
        except (URLError, OSError, ValueError):
            time.sleep(0.15)
    return False


def run(directory):
    identity = hashlib.sha256(os.fsencode(os.path.normcase(str(directory)))).hexdigest()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # Reserve the port before creating any database or log. On Windows an
        # exclusive bind also prevents a second program from sharing our port.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((HOST, PORT))
        except OSError:
            if same_instance(identity):
                open_library()
                return 0
            notice(f"El puerto {PORT} está ocupado. Cierra la otra copia o la aplicación que lo utiliza.")
            return 1

        data = directory / "JMangaShelf-datos"
        data.mkdir(exist_ok=True)
        handler = RotatingFileHandler(data / "aplicacion.log", maxBytes=128 * 1024,
                                      backupCount=1, encoding="utf-8", delay=True)
        logging.basicConfig(handlers=[handler], level=logging.WARNING,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        app = create_app(data / "library.sqlite3")
        app.config.update(PORTABLE=True, INSTANCE_TOKEN=secrets.token_hex(24))
        stopping = Event()

        @app.before_request
        def current_window():
            if stopping.is_set():
                abort(503, "JMangaShelf se está cerrando.")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get("X-JMangaShelf-Instance") != app.config["INSTANCE_TOKEN"]:
                    abort(409, "La aplicación se reinició. Recarga esta pestaña.")

        @app.get("/api/portable/instance")
        def instance():
            return jsonify(instance=identity)

        @app.post("/api/portable/shutdown")
        def shutdown():
            stopping.set()
            return "", 204

        channels = {}
        server = create_server(app, sockets=[listener], map=channels,
                               max_request_body_size=app.config["MAX_CONTENT_LENGTH"],
                               expose_tracebacks=False, ident="JMangaShelf")
        Thread(target=open_library, daemon=True).start()
        try:
            while not stopping.is_set():
                wasyncore.loop(timeout=1, map=channels, count=1)
            # Flush the shutdown response and finish in-flight transactions.
            server.accepting = False
            while any(c.requests or c.total_outbufs_len for c in server.active_channels.values()):
                wasyncore.loop(timeout=0.1, map=channels, count=1)
        finally:
            server.task_dispatcher.shutdown()
            wasyncore.close_all(channels)
    return 0


if __name__ == "__main__":
    directory = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    try:
        raise SystemExit(run(directory))
    except Exception as error:
        logging.getLogger(__name__).error("Fallo de inicio: %s", type(error).__name__)
        notice(f"No se pudo iniciar JMangaShelf ({type(error).__name__}). Comprueba que la carpeta "
               f"sea escribible y que haya espacio disponible.\n\n{error}")
        raise SystemExit(1) from None
