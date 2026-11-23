"""Run the existing development launcher without a console on Windows."""
import ctypes
import traceback
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

LOG = Path(__file__).resolve().parent / "build" / "desarrollo.log"


def notice(message):
    ctypes.windll.user32.MessageBoxW(None, message, "JMangaShelf · Desarrollo", 0x40)


def open_browser(url):
    try:
        if webbrowser.open(url):
            return True
    except (OSError, webbrowser.Error):
        traceback.print_exc()
    notice(f"Abre {url} en tu navegador.")
    return True


def main():
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8", buffering=1) as output:
            with redirect_stdout(output), redirect_stderr(output):
                try:
                    from launcher import PORT, launch

                    if launch(open_browser=open_browser, windowless=True) == 0:
                        return 0
                    reason = f"Comprueba si el puerto {PORT} está ocupado."
                except Exception as error:
                    traceback.print_exc()
                    reason = f"Comprueba el entorno .venv. Error: {type(error).__name__}."
        notice(f"No se pudo iniciar JMangaShelf. {reason}\n\nRegistro: {LOG}")
    except OSError as error:
        notice(f"No se pudo abrir el registro {LOG}.\n\n{error}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
