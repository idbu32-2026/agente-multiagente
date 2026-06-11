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
    & "$proyecto\.venv\Scripts\python.exe" "$proyecto\voz\jarvis_voz.py"
    if ($LASTEXITCODE -eq 0) { break }   # salida normal: no reiniciar

    $caidas++
    Add-Content $registroCaidas "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Jarvis se cerro con codigo $LASTEXITCODE (caida $caidas)."
    if ($caidas -ge 5) {
        Read-Host "Jarvis ha fallado $caidas veces. Mira el error de arriba o logs\jarvis-caidas.log. Pulsa Enter para cerrar"
        break
    }
    Write-Host ""
    Write-Host "Jarvis se ha caido (codigo $LASTEXITCODE). Reiniciando en 5 segundos... (Ctrl+C para cancelar)"
    Start-Sleep -Seconds 5
}
