@echo off
rem TRAVIS - tablero de comunicacion para ninos.
rem Abre la app a PANTALLA COMPLETA (modo kiosko: el nino no puede salir
rem tocando la pantalla). Para cerrar: Alt+F4 (lo hace el adulto).

set EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
if not exist "%EDGE%" set EDGE=C:\Program Files\Microsoft\Edge\Application\msedge.exe

if exist "%EDGE%" (
  start "" "%EDGE%" --kiosk "%~dp0travis\travis.html" --edge-kiosk-type=fullscreen
) else (
  rem Sin Edge: abre en el navegador por defecto (sin kiosko)
  start "" "%~dp0travis\travis.html"
)
