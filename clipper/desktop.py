"""Programmfenster: Clipper + Dashboard in einem eigenen Fenster statt im Browser.

Nutzt pywebview (unter Windows Edge WebView2, ist in Windows 11 dabei). Das Dashboard bleibt zusätzlich im
Netzwerk erreichbar (z.B. vom MacBook über http://<PC-IP>:8686).

Start: Desktop-Verknüpfung „ClipStream“ oder `python -m clipper app`.
Verknüpfung anlegen: `python -m clipper shortcut`.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .util import IS_WINDOWS, NO_WINDOW

log = logging.getLogger("clipper.desktop")

ROOT = Path(__file__).resolve().parent.parent
ICON = Path(__file__).resolve().parent / "web" / "static" / "icon.ico"

LOCALIZATION = {
    "global.quitConfirmation": "ClipStream beenden?\n\nÜberwachung und Aufnahmen werden gestoppt – solange das "
                               "Programm zu ist, werden keine Clips erstellt.",
    "global.ok": "OK",
    "global.quit": "Beenden",
    "global.cancel": "Abbrechen",
    "global.saveFile": "Datei speichern",
}


def _reload_path() -> None:
    """Eine Verknüpfung kann einen alten PATH erben (z.B. direkt nach `winget install ffmpeg`) – aus der Registry ergänzen."""
    if not IS_WINDOWS or shutil.which("ffmpeg"):
        return
    import winreg

    extra = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                extra.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), *extra])


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def run_desktop(cfg: dict) -> None:
    import webview

    from .web.app import desktop_token

    _reload_path()
    port = int(cfg["web"]["port"])
    server = orchestrator = None
    if _port_in_use(port):
        # z.B. läuft start.bat schon – dann nur ein Fenster dazu öffnen, nichts doppelt starten
        log.info("Clipper läuft schon auf Port %d – öffne nur das Fenster", port)
    else:
        import uvicorn

        from .cli import start_services

        _db, orchestrator, app, lan_url = start_services(cfg)
        server = uvicorn.Server(uvicorn.Config(app, host=cfg["web"]["host"], port=port, log_level="warning"))
        threading.Thread(target=server.run, name="web", daemon=True).start()
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        if not server.started:
            log.error("Dashboard-Server startet nicht (Port %d belegt?)", port)
        in_lan = lan_url and cfg["web"]["host"] in ("0.0.0.0", "::")
        log.info("ClipStream läuft im Programmfenster%s", f" · im Netzwerk: {lan_url}" if in_lan else "")

    if IS_WINDOWS:
        try:  # eigenes Taskleisten-Symbol statt dem von Python
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClipStream.Dashboard")
        except Exception:  # noqa: BLE001
            pass

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.create_window(
        "ClipStream", f"http://127.0.0.1:{port}/desktop-login?token={desktop_token(cfg)}",
        width=1380, height=880, min_size=(980, 640), background_color="#0a0c11",
        confirm_close=server is not None, text_select=True,
    )
    try:
        webview.start(icon=str(ICON) if ICON.exists() else None, localization=LOCALIZATION)
    finally:
        if server:
            server.should_exit = True
        if orchestrator:
            orchestrator.stop()
        log.info("Programmfenster geschlossen")


def create_shortcut() -> str:
    """Verknüpfungen „ClipStream“ auf dem Desktop und im Startmenü anlegen (Windows)."""
    if not IS_WINDOWS:
        raise RuntimeError("Verknüpfungen gibt es nur unter Windows – auf dem Mac ./start.sh benutzen")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise RuntimeError(f"pythonw.exe nicht gefunden neben {sys.executable}")

    def q(value) -> str:
        return str(value).replace("'", "''")

    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {\n"
        "  Remove-Item -ErrorAction SilentlyContinue (Join-Path $dir 'GTA Clipper.lnk')\n"  # alter Name
        "  $s = $ws.CreateShortcut((Join-Path $dir 'ClipStream.lnk'))\n"
        f"  $s.TargetPath = '{q(pythonw)}'\n"
        "  $s.Arguments = '-m clipper app'\n"
        f"  $s.WorkingDirectory = '{q(ROOT)}'\n"
        f"  $s.IconLocation = '{q(ICON)},0'\n"
        "  $s.Description = 'ClipStream – Stream-Highlights zu Shorts'\n"
        "  $s.Save()\n"
        "  Write-Output $s.FullName\n"
        "}\n"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                         capture_output=True, text=True, creationflags=NO_WINDOW, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"Verknüpfung konnte nicht angelegt werden: {res.stderr.strip()}")
    return " und ".join(line for line in res.stdout.splitlines() if line.strip())
