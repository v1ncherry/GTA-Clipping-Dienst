@echo off
REM ClipStream starten (Windows). Beim ersten Start wird alles eingerichtet.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 goto :nopython

where ffmpeg >nul 2>&1
if errorlevel 1 goto :noffmpeg

if not exist .venv\Scripts\python.exe (
  echo [1/2] Erstinstallation - das dauert ein paar Minuten ...
  python -m venv .venv
  if errorlevel 1 goto :error
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto :error
)

if not exist config.yaml (
  echo [2/2] Einrichtung ...
  .venv\Scripts\python.exe -m clipper setup
  if errorlevel 1 goto :error
)

.venv\Scripts\python.exe -m clipper %*
pause
exit /b 0

:nopython
echo Python fehlt - wird jetzt mit winget installiert ...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo Fertig. Bitte dieses Fenster schliessen und start.bat noch einmal starten.
pause
exit /b 1

:noffmpeg
echo ffmpeg fehlt - wird jetzt mit winget installiert ...
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
echo.
echo Fertig. Bitte dieses Fenster schliessen und start.bat noch einmal starten.
pause
exit /b 1

:error
echo.
echo Da ist etwas schiefgegangen - bitte die Meldung oben kopieren und schicken.
pause
exit /b 1
