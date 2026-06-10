@echo off
REM Lanzador del Sistema Multiagente: arranca el servidor y abre el navegador.
cd /d "%~dp0"

REM Arranca el servidor en una ventana MINIMIZADA (no la cierres mientras lo usas).
REM Se usa python.exe (no pythonw) porque uvicorn necesita una consola.
start "Servidor Agente - NO CERRAR" /min "%~dp0.venv\Scripts\python.exe" run.py

REM Espera unos segundos a que el servidor este listo y abre la pagina.
REM (ping como pausa fiable; 'timeout' falla si la entrada esta redirigida)
ping -n 5 127.0.0.1 >nul
start "" "http://127.0.0.1:8000"
