@echo off
REM Detiene el Sistema Multiagente (cierra el servidor que escucha en el puerto 8000).
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
echo Agente detenido. Puedes cerrar esta ventana.
ping -n 3 127.0.0.1 >nul
