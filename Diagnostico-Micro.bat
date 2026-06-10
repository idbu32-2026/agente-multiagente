@echo off
REM Diagnostico: prueba los 3 microfonos y mide cual oye tu voz.
REM Guarda el resultado en voz\diag.log (Claude lo lee).
cd /d "%~dp0"
title Diagnostico de microfono

echo.
echo  ============================================================
echo   Voy a probar tus 3 microfonos, uno tras otro.
echo   IMPORTANTE: empieza a HABLAR YA y NO PARES.
echo   Repite en voz alta:  "hola hola, me oyes, hola hola"
echo   durante todo el rato (aprox. 30-40 segundos). No te calles.
echo  ============================================================
echo.

del "voz\diag.log" 2>nul

echo ===== MICRO 0 =====>> "voz\diag.log"
"%~dp0.venv\Scripts\python.exe" -u voz\test_micro.py 0 >> "voz\diag.log" 2>&1
echo ===== MICRO 1 =====>> "voz\diag.log"
"%~dp0.venv\Scripts\python.exe" -u voz\test_micro.py 1 >> "voz\diag.log" 2>&1
echo ===== MICRO 2 =====>> "voz\diag.log"
"%~dp0.venv\Scripts\python.exe" -u voz\test_micro.py 2 >> "voz\diag.log" 2>&1

echo.
echo  --- LISTO. Avisa a Claude para que lea el resultado. ---
pause
