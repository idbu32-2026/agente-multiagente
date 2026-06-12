# Sube el proyecto JARVIS a tu cuenta de GitHub como repositorio PRIVADO.
# Pensado para ejecutarse con doble clic desde el acceso directo del escritorio.
# Si el repositorio ya existe, simplemente sube los commits nuevos (git push).

$host.UI.RawUI.WindowTitle = "Subiendo JARVIS a GitHub..."
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$proyecto = "C:\Users\travi\proyectos\agente-multiagente"
$repoUrl = "https://github.com/idbu32-2026/agente-multiagente"

Set-Location $proyecto

# Existe ya el repositorio remoto?
& $gh repo view idbu32-2026/agente-multiagente --json url 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "El repositorio ya existe: subiendo los commits nuevos..."
    git push -u origin main 2>&1 | Write-Host
} else {
    Write-Host "Creando el repositorio privado y subiendo todo el proyecto..."
    & $gh repo create agente-multiagente --private --source . --push 2>&1 | Write-Host
}

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "================================================="
    Write-Host "  LISTO. Tu proyecto esta en:"
    Write-Host "  $repoUrl"
    Write-Host "================================================="
    Start-Process $repoUrl
} else {
    Write-Host ""
    Write-Host "Algo fallo. Hazle una foto a esta ventana o copia el"
    Write-Host "texto del error y enseñaselo a Claude."
}

Read-Host "Pulsa Enter para cerrar esta ventana"
