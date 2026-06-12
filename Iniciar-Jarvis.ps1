# JARVIS - arranca el asistente de voz (modo charla con busqueda web).
# Se lanza desde el acceso directo "JARVIS" del escritorio (los .bat no se
# ejecutan por doble clic en este PC; PowerShell si).
# MIC_INDEX y JARVIS_GAIN se leen del .env del proyecto.
#
# AUTO-REINICIO: si Jarvis se cae (error), se vuelve a levantar solo, hasta 5
# veces. Salida normal ('apagate', adios o Ctrl+C) cierra de verdad. Cada caida
# queda anotada en logs\jarvis-caidas.log.

$proyecto = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proyecto
$host.UI.RawUI.WindowTitle = "JARVIS (di 'Hey Jarvis' - Ctrl+C para salir)"

New-Item -ItemType Directory -Force "$proyecto\logs" | Out-Null
$registroCaidas = "$proyecto\logs\jarvis-caidas.log"
$caidas = 0

while ($true) {
    $inicio = Get-Date
    & "$proyecto\.venv\Scripts\python.exe" "$proyecto\voz\jarvis_voz.py"
    if ($LASTEXITCODE -eq 0) { break }   # salida normal: no reiniciar

    # Si Jarvis corrio un buen rato antes de cerrarse, NO es un bucle de
    # fallo: contador a cero. (Leccion 2026-06-12: los reinicios manuales
    # durante mantenimiento gastaban los 5 intentos y el vigilante se
    # rendia esperando una tecla en una ventana minimizada que nadie ve.)
    if (((Get-Date) - $inicio).TotalSeconds -ge 120) { $caidas = 0 }

    $caidas++
    Add-Content $registroCaidas "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Jarvis se cerro con codigo $LASTEXITCODE (caida $caidas)."
    if ($caidas -ge 5) {
        Add-Content $registroCaidas "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] 5 caidas rapidas seguidas: el vigilante se rinde para no ciclar. Arranca a mano con JARVIS.lnk tras revisar el error."
        break
    }
    Write-Host ""
    Write-Host "Jarvis se ha caido (codigo $LASTEXITCODE). Reiniciando en 5 segundos... (Ctrl+C para cancelar)"
    Start-Sleep -Seconds 5
}
