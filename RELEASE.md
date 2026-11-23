# JMangaShelf v1.0.0 · Windows x64

## Novedades

- La biblioteca añade la siguiente tanda al acercarte al final. El botón **Cargar más obras** también funciona con el teclado. Editar conserva las tarjetas cargadas y la posición.
- **Marcadores del navegador** admite HTML Netscape con carpetas anidadas o enlaces en la raíz. Se conservan la revisión previa y las decisiones manuales sobre duplicados.
- Nuevo icono de estantería en Windows, pestañas y marcadores. El ejecutable incluye el número de versión y usa el navegador predeterminado.
- La distribución portable incorpora Python, Waitress, Flask, SQLite y los recursos web. Sirve la biblioteca en la interfaz de bucle local. La base sigue usando el esquema 4.

## Compilar desde las fuentes

En Windows x64 con Python 3.14 x64, abre PowerShell en la raíz del código y ejecuta los siguientes comandos. La instalación inicial de herramientas requiere acceso a PyPI.

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python -m unittest discover -s tests -v
.\.venv-build\Scripts\python tools/package_release.py
.\.venv-build\Scripts\python tools/smoke_portable.py release/v1.0.0/JMangaShelf-1.0.0-Windows-x64.zip
```

El empaquetador usa PyInstaller en modo carpeta y solo toma los archivos revisados de `tools/public-files.txt`. Requiere un destino nuevo. Para repetir la compilación indica, por ejemplo, `--output release/verificacion`. Para regenerar los iconos ejecuta `python tools/generate_icons.py` con Pillow instalado. La prueba del ejecutable requiere Chrome local y el puerto 8766 libre. Extrae el ZIP en una carpeta temporal.

## Archivos de entrega

- `JMangaShelf-1.0.0-Windows-x64.zip` contiene la carpeta `JMangaShelf` con el ejecutable, `_internal` con el runtime y los recursos web, la documentación y `licencias`. La carpeta de datos se crea en el primer inicio.
- `JMangaShelf-1.0.0-fuentes.zip` contiene el código, las pruebas sintéticas, las instrucciones y las herramientas de compilación.
- `SHA256SUMS.txt` permite comparar ambos archivos mediante `Get-FileHash -Algorithm SHA256`.

## Verificación y limitaciones

El portable publicado corresponde a la compilación v1.0.0 validada. El repositorio también documenta el iniciador que ejecuta el código fuente con el entorno de desarrollo local. El ejecutable del ZIP carece de firma Authenticode. La prueba en otro equipo Windows x64 sin Python continúa pendiente. Las búsquedas de portadas dependen de la API de AniList. [Licencias y condiciones](TERCEROS.md)
