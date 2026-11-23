"""Start the localhost application and open it once HTTP is ready."""

import json
import secrets
import socket
import time
import webbrowser
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from flask import abort, request
from werkzeug.serving import ThreadedWSGIServer, make_server

from app import create_app

HOST = "127.0.0.1"
PORT = 8765


class LocalOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ready(url):
    local = build_opener(ProxyHandler({}), LocalOnly())
    try:
        with local.open(url + "api/health", timeout=0.6) as response:
            return json.loads(response.read(256)) == {"application": "JMangaShelf"}
    except HTTPError as error:
        error.close()
        if error.code != 404:
            return False
        # An older instance can already be running on the same port.
        try:
            with local.open(url, timeout=0.6) as response:
                html = response.read(16384)
                return b"<title>JMangaShelf</title>" in html and b"/static/organization.js" in html
        except (URLError, OSError):
            return False
    except (URLError, OSError, ValueError):
        return False


def occupied(port):
    try:
        with socket.create_connection((HOST, port), timeout=0.3):
            return True
    except OSError:
        return False


def open_when_ready(url, open_browser, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if ready(url):
            try:
                if open_browser(url):
                    return True
            except (OSError, webbrowser.Error):
                pass
            print(f"Abre {url} en tu navegador.", flush=True)
            return True
        time.sleep(0.2)
    return False


class WindowlessServer(ThreadedWSGIServer):
    daemon_threads = False
    allow_reuse_address = False

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def development_server(port, app_factory):
    # Reserve the port before opening SQLite, including on simultaneous launches.
    server = WindowlessServer(HOST, port, None)
    try:
        app = app_factory()
        token = secrets.token_hex(24)
        app.config.update(DEV_SHUTDOWN=True, INSTANCE_TOKEN=token)

        @app.post("/api/development/shutdown")
        def shutdown():
            if request.headers.get("X-JMangaShelf-Instance") != token:
                abort(409, "Recarga esta pestaña antes de cerrar JMangaShelf.")
            response = app.response_class(status=204)
            response.call_on_close(server.shutdown)
            return response

        server.app = app
        return server
    except BaseException:
        server.server_close()
        raise


def launch(port=PORT, app_factory=create_app, open_browser=webbrowser.open, *, windowless=False):
    url = f"http://{HOST}:{port}/"
    if port and occupied(port):
        if open_when_ready(url, open_browser, timeout=3):
            print("JMangaShelf ya está abierto.", flush=True)
            return 0
        print(f"El puerto {port} está en uso y no respondió como JMangaShelf.", flush=True)
        return 1
    try:
        server = (development_server(port, app_factory) if windowless
                  else make_server(HOST, port, app_factory(), threaded=True))
    except SystemExit:
        # Another launch may have bound the port while this process was preparing.
        return 0 if port and open_when_ready(url, open_browser, timeout=3) else 1
    except Exception as error:
        if windowless:
            raise
        print(f"No se pudo iniciar JMangaShelf. {error}", flush=True)
        return 1
    with server:
        url = f"http://{HOST}:{server.server_port}/"
        stop_hint = "Usa Cerrar JMangaShelf en el navegador." if windowless else "Pulsa Ctrl+C en esta ventana para detenerlo."
        print(f"JMangaShelf en {url} · {stop_hint}", flush=True)

        def open_window():
            if not open_when_ready(url, open_browser):
                print(f"JMangaShelf tardó en responder. Abre {url} cuando esté disponible.", flush=True)

        Thread(target=open_window, daemon=True).start()
        server.serve_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(launch())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
