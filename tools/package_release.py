"""Build only from the reviewed public source manifest, into a new destination."""
import argparse
import hashlib
import importlib.metadata as md
from itertools import takewhile
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import VERSION

DOCS = ("README.md", "LICENSE", "TERCEROS.md", "RELEASE.md")
RUNTIME = ("Flask", "Werkzeug", "Jinja2", "MarkupSafe", "itsdangerous", "click",
           "blinker", "waitress", "Pillow", "colorama", "PyInstaller")


def pack(folder, archive):
    with ZipFile(archive, "x", ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(folder.parent))
    with ZipFile(archive) as zipped:
        if zipped.testzip():
            raise ValueError("Archivo ZIP dañado.")


def notices(dest):
    dest.mkdir()
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", dest / "Python-Windows.txt")
    for package in RUNTIME:
        dist = md.distribution(package)
        files = [f for f in dist.files if ".dist-info/" in str(f)
                 and f.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
        if not files:
            raise ValueError(f"Falta la licencia de {package}.")
        for item in files:
            shutil.copyfile(dist.locate_file(item), dest / f"{package}-{dist.version}-{item.name}.txt")

    # The wheel's ZPL text refers to separate notices in its module headers.
    waitress = md.distribution("waitress")
    headers = []
    for module in ("buffers", "runner", "trigger", "wasyncore"):
        code = waitress.locate_file(f"waitress/{module}.py").read_text(encoding="utf-8")
        header = "\n".join(takewhile(lambda line: not line.strip() or line.startswith("#"), code.splitlines())).strip()
        if "Copyright" not in header:
            raise ValueError("Falta el aviso de Waitress.")
        headers.append(header)
    (dest / "Waitress-avisos.txt").write_text("\n\n".join(headers), encoding="utf-8")


def build(destination):
    if sys.platform != "win32" or platform.machine().upper() not in {"AMD64", "X86_64"} or sys.maxsize < 2**32:
        raise SystemExit("Compila con Python para Windows x64.")
    if destination.exists():
        raise SystemExit("El destino ya existe. Indica una carpeta de salida nueva.")
    workdir = ROOT / "build"
    workdir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=workdir) as scratch:
        work = Path(scratch)
        source = work / f"JMangaShelf-{VERSION}-fuentes"
        allowed = (ROOT / "tools/public-files.txt").read_text(encoding="utf-8").splitlines()
        for name in allowed:
            path = ROOT / name
            if not path.resolve().is_relative_to(ROOT) or path.resolve() != path:
                raise ValueError(f"Ruta no admitida: {name}")
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        out = work / "salida"
        out.mkdir()
        pack(source, out / (source.name + ".zip"))
        env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work / "cache"), PYTHONNOUSERSITE="1")
        subprocess.run([sys.executable, "-I", "-m", "PyInstaller", "--onedir", "--windowed", "--noupx",
                        "--name", "JMangaShelf", "--icon", "static/icons/JMangaShelf.ico",
                        "--version-file", "tools/windows-version.txt", "--add-data", "templates:templates",
                        "--add-data", "static:static", "--exclude-module", "tkinter",
                        "--exclude-module", "setuptools", "--exclude-module", "IPython",
                        "--distpath", str(out), "--workpath", str(work / "pyi"), "--specpath", str(source),
                        "portable.py"], cwd=source, env=env, check=True)
        bundle = out / "JMangaShelf"
        for doc in DOCS:
            shutil.copyfile(source / doc, bundle / doc)
        notices(bundle / "licencias")
        pack(bundle, out / f"JMangaShelf-{VERSION}-Windows-x64.zip")
        hashes = []
        for archive in sorted(out.glob("*.zip")):
            with archive.open("rb") as stream:
                hashes.append(hashlib.file_digest(stream, "sha256").hexdigest() + "  " + archive.name)
        (out / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="ascii")
        destination.parent.mkdir(parents=True, exist_ok=True)
        out.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "release" / f"v{VERSION}")
    build(parser.parse_args().output.resolve())
