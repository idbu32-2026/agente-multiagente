# JARVIS - arranca el asistente de voz (modo charla con busqueda web).
# Se lanza desde el acceso directo "JARVIS" del escritorio (los .bat no se
# ejecutan por doble clic en este PC; PowerShell si).
# MIC_INDEX y JARVIS_GAIN se leen del .env del proyecto.

$proyecto = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proyecto
$host.UI.RawUI.WindowTitle = "JARVIS (di 'Hey Jarvis' - Ctrl+C para salir)"
& "$proyecto\.venv\Scripts\python.exe" "$proyecto\voz\jarvis_voz.py"
# Si Jarvis se cierra con error, deja la ventana abierta para poder leerlo.
if ($LASTEXITCODE -ne 0) { Read-Host "Jarvis termino con error (codigo $LASTEXITCODE). Pulsa Enter para cerrar" }
