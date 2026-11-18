@echo off
REM GTA Clipper starten (Windows). Beim ersten Mal wird alles installiert.
cd /d "%~dp0"
if not exist .venv (
  echo Erstinstallation ...
  python -m venv .venv || goto :error
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt || goto :error
) else (
  call .venv\Scripts\activate.bat
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
python -m clipper %*
pause
exit /b 0
:error
echo Installation fehlgeschlagen. Ist Python 3.11+ installiert und im PATH?
pause
