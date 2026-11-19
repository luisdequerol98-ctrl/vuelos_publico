@echo off
rem Arranca el buscador de vuelos en segundo plano y abre la pagina.
rem Si ya estaba abierto, lo cierra antes (sirve para reiniciarlo tras una actualizacion).
cd /d "%~dp0"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5077" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
start "" ".venv\Scripts\pythonw.exe" app.py --abrir
