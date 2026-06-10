@echo off
REM Ejecuta el boletin de novedades de MotoGP y lo envia por WhatsApp.
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0news_motogp.py"
