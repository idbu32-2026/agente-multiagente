@echo off
REM Lanzador de voz: abre Jarvis para hablarle por el microfono.
REM Doble clic para usar. Di "hey jarvis" cuando este listo. Ctrl+C para salir.
cd /d "%~dp0"
title Jarvis - voz (NO CERRAR mientras hablas)

REM Micro a usar (0 = USB Audio Device). Si no te oye, prueba 1 o 2.
set MIC_INDEX=0

echo.
echo  Arrancando Jarvis... esto tarda unos segundos (carga el oido).
echo  Cuando veas que esta listo, di en voz alta:  "hey jarvis"
echo  Para salir: pulsa Ctrl+C o cierra esta ventana.
echo.

"%~dp0.venv\Scripts\python.exe" voz\jarvis_voz.py

REM Si el programa termina o falla, deja la ventana abierta para ver el mensaje.
echo.
echo  --- Jarvis se ha detenido. Lee arriba si hubo algun error. ---
pause
