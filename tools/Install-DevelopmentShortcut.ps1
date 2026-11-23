param([switch]$BuildOnly)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root 'JMangaShelf-Desarrollo.exe'
$icon = Join-Path $root 'static\icons\JMangaShelf.ico'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

& $compiler /nologo /codepage:65001 /target:winexe /platform:anycpu /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$exe" (Join-Path $PSScriptRoot 'DevelopmentLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar el iniciador de desarrollo.' }
if ($BuildOnly) { return }

$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'No se encontro el Escritorio.' }

$shortcutPath = Join-Path $desktop 'JMangaShelf Desarrollo.lnk'
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcutPath)
if ((Test-Path -LiteralPath $shortcutPath) -and ($link.TargetPath -ne $exe)) {
    throw "Ya existe un acceso directo distinto en $shortcutPath"
}
$link.TargetPath = $exe
$link.WorkingDirectory = $root
$link.IconLocation = "$icon,0"
$link.Description = 'JMangaShelf - Biblioteca de desarrollo'
$link.Save()
$saved = $shell.CreateShortcut($shortcutPath)
if ($saved.TargetPath -ne $exe -or $saved.WorkingDirectory -ne $root -or $saved.IconLocation -ne "$icon,0") {
    throw 'El destino, la carpeta de trabajo o el icono del acceso directo no coincide.'
}
$saved | Select-Object FullName, TargetPath, WorkingDirectory, IconLocation
