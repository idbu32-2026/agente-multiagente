# JARVIS - arranca el asistente de voz en modo ACTUAR (charla, busca en la
# web Y ejecuta ordenes por voz sobre el PC, pidiendo permiso por voz antes
# de acciones sensibles; borrar esta prohibido por codigo).
# Se lanza desde el acceso directo "JARVIS" del escritorio (los .bat no se
# ejecutan por doble clic en este PC; PowerShell si).
# MIC_INDEX y JARVIS_GAIN se leen del .env del proyecto.
#
# SUPERVISOR RESILIENTE (rediseño 13-jun, ingenieria senior). Un asistente
# "siempre encendido" NUNCA debe quedar muerto y en silencio. Reglas:
#   - Salida limpia (codigo 0: 'apagate'/adios/Ctrl+C) -> cerrar de verdad.
#   - Reinicio planificado de audio (codigo 3) -> relanzar enseguida; NO
#     cuenta como caida... salvo que se repita en bucle (entonces escala).
#   - Caida real -> reintento con BACKOFF EXPONENCIAL (no martillea la CPU).
#   - Tras varias caidas rapidas NO se rinde: pasa a reintento lento y AVISA
#     (HUD + globo de Windows + log), para autocurarse cuando se resuelva la
#     causa (p. ej. micro reconectado).
# Leccion 13-jun: el supervisor viejo hacia 'break' tras 5 caidas y dejo a
# JARVIS muerto 16 h sin que nadie se enterara. Eso ya no puede pasar.

$proyecto = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proyecto
$host.UI.RawUI.WindowTitle = "JARVIS (di 'Jarvis' - Ctrl+C para salir)"

New-Item -ItemType Directory -Force "$proyecto\logs" | Out-Null
$registroCaidas = "$proyecto\logs\jarvis-caidas.log"
$estadoJson     = "$proyecto\hud\estado.json"

# --- Parametros de resiliencia ---
$backoffMin = 5     # primer reintento tras una caida (s)
$backoffMax = 300   # tope de espera entre reintentos (s) = 5 min
$rapidasMax = 5     # caidas rapidas seguidas -> modo degradado (reintento lento)
$buenRato   = 120   # s: si corrio mas de esto, la racha se considera sana
$reinicios3Max = 4  # reinicios de audio (cod.3) seguidos antes de tratarlo como fallo

function Anotar($msg) {
    Add-Content $registroCaidas "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
}

function Publicar-Estado($estado, $mensaje) {
    # Publica en el MISMO canal que lee el HUD (hud/estado.json) para no
    # quedarse 'apagado' y mudo: el HUD mostrara el mensaje en vivo.
    try {
        $obj = [ordered]@{ estado = $estado; usuario = ""; jarvis = $mensaje
                           ts = [DateTimeOffset]::Now.ToUnixTimeSeconds() }
        ($obj | ConvertTo-Json -Compress) | Set-Content -Path $estadoJson -Encoding utf8
    } catch {}
}

function Avisar-Globo($titulo, $texto) {
    # Globo de notificacion de Windows. Best-effort: si falla, no pasa nada.
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $ni = New-Object System.Windows.Forms.NotifyIcon
        $ni.Icon = [System.Drawing.SystemIcons]::Warning
        $ni.Visible = $true
        $ni.ShowBalloonTip(10000, $titulo, $texto, [System.Windows.Forms.ToolTipIcon]::Warning)
        Start-Sleep -Milliseconds 300
        $ni.Dispose()
    } catch {}
}

$caidas = 0          # caidas reales rapidas seguidas
$reinicios3 = 0      # reinicios de audio (codigo 3) rapidos seguidos
$avisado = $false    # ya se aviso del modo degradado en esta racha

while ($true) {
    $inicio = Get-Date
    & "$proyecto\.venv\Scripts\python.exe" "$proyecto\voz\jarvis_voz.py" --actuar
    $codigo = $LASTEXITCODE
    $duracion = ((Get-Date) - $inicio).TotalSeconds

    # 1) Salida limpia: 'apagate' / adios / Ctrl+C -> cerrar de verdad.
    if ($codigo -eq 0) { break }

    # 2) Codigo 3 = reinicio PLANIFICADO para reabrir el audio. No es una
    #    caida y no gasta presupuesto... PERO si se repite en bucle (reabrir
    #    el audio no arregla nada, justo el patron de la noche del 12->13),
    #    se escala a fallo real para que entre el backoff y el aviso.
    if ($codigo -eq 3) {
        if ($duracion -ge $buenRato) { $reinicios3 = 0 }
        $reinicios3++
        if ($reinicios3 -le $reinicios3Max) {
            Anotar "Reinicio planificado de audio (codigo 3, $reinicios3/$reinicios3Max). Relanzo en 3 s."
            Start-Sleep -Seconds 3
            continue
        }
        Anotar "Codigo 3 en bucle ($reinicios3 seguidos): reabrir el audio NO arregla nada. Lo trato como fallo real."
        # cae al manejo de fallo de abajo
    } else {
        $reinicios3 = 0
    }

    # 3) Fallo real. Si corrio un buen rato, la racha estaba sana: a cero.
    if ($duracion -ge $buenRato) { $caidas = 0; $avisado = $false }
    $caidas++
    Anotar "Jarvis se cerro con codigo $codigo tras $([int]$duracion)s (caida $caidas)."

    # 4) Backoff exponencial con tope. NUNCA se rinde.
    if ($caidas -lt $rapidasMax) {
        $espera = [int][Math]::Min($backoffMin * [Math]::Pow(2, $caidas - 1), $backoffMax)
        Publicar-Estado "apagado" "Jarvis se reinicia (intento $caidas)..."
        Write-Host ""
        Write-Host "Jarvis se ha caido (codigo $codigo). Reintento en $espera s... (Ctrl+C para cancelar)"
    } else {
        $espera = $backoffMax
        if (-not $avisado) {
            Anotar "$rapidasMax caidas rapidas seguidas: entro en REINTENTO LENTO (cada $backoffMax s). NO me rindo; me autocurare cuando se resuelva la causa. Revisa logs\jarvis.log."
            Publicar-Estado "apagado" "Jarvis no arranca bien; reintento cada 5 min. Revisa el microfono o los logs."
            Avisar-Globo "JARVIS necesita ayuda" "Lleva varias caidas seguidas. Sigo reintentando cada 5 minutos. Revisa el microfono o el archivo logs\jarvis.log."
            $avisado = $true
        }
        Write-Host ""
        Write-Host "Jarvis sigue cayendo. Reintento lento en $espera s... (Ctrl+C para parar)"
    }
    Start-Sleep -Seconds $espera
}
