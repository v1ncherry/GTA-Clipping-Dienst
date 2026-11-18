"""Kommandozeile: python -m clipper <befehl>"""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .db import DB
from .util import IS_MAC_ARM, ffmpeg_bin, setup_logging


def _db(cfg: dict) -> DB:
    return DB(str(Path(cfg["data_dir"]) / "clipper.db"))


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def cmd_run(cfg: dict, _args) -> None:
    import uvicorn

    from .monitor import Orchestrator
    from .processing import Processor
    from .uploaders import UploadManager
    from .web.app import create_app

    db = _db(cfg)
    processor = Processor(cfg, db)
    processor.start()
    orchestrator = Orchestrator(cfg, db, processor)
    orchestrator.start()
    app = create_app(cfg, db, orchestrator, processor, UploadManager(cfg, db))
    host, port = cfg["web"]["host"], int(cfg["web"]["port"])
    print(f"\n  GTA Clipper {__version__} läuft")
    print(f"  Dashboard:        http://localhost:{port}")
    ip = _lan_ip()
    if ip and host in ("0.0.0.0", "::"):
        print(f"  Im Netzwerk (Mac): http://{ip}:{port}")
        if not cfg["web"].get("password"):
            print("  ⚠ Kein Passwort gesetzt (web.password in config.yaml) – jeder im WLAN kann hochladen!")
    n = len(db.list_streamers(enabled_only=True))
    print(f"  Überwache {n} Streamer. Beenden mit Strg+C.\n")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        orchestrator.stop()


def cmd_add(cfg: dict, args) -> None:
    from .platforms import detect_platform

    platform, channel, url = detect_platform(args.url)
    db = _db(cfg)
    name = args.name or channel
    settings = {"transcription": {"language": args.language}} if args.language else {}
    db.add_streamer(name, url, platform, channel, settings)
    print(f"✓ {name} hinzugefügt ({platform}: {url})")


def cmd_list(cfg: dict, _args) -> None:
    db = _db(cfg)
    rows = db.list_streamers()
    if not rows:
        print("Noch keine Streamer. Hinzufügen mit: python -m clipper add <url>")
    for s in rows:
        print(f"{'●' if s['enabled'] else '○'} {s['name']:<20} {s['platform']:<8} {s['url']}")
    accounts = db.list_accounts()
    if accounts:
        print("\nAccounts:")
        for a in accounts:
            print(f"  [{a['id']}] {a['platform']:<10} {a['name']}")


def cmd_remove(cfg: dict, args) -> None:
    db = _db(cfg)
    s = db.get_streamer_by_name(args.name)
    if not s:
        sys.exit(f"Streamer '{args.name}' nicht gefunden")
    db.delete_streamer(s["id"])
    print(f"✓ {s['name']} entfernt")


def cmd_vod(cfg: dict, args) -> None:
    from .processing import Processor
    from .vod import run_vod

    db = _db(cfg)
    streamer = db.get_streamer_by_name(args.streamer)
    if not streamer:
        sys.exit(f"Streamer '{args.streamer}' nicht gefunden – erst mit 'add' anlegen (für Credits & Hashtags)")
    processor = Processor(cfg, db)
    processor.start()
    ids = run_vod(cfg, db, processor, args.source, streamer, args.chat, args.lag)
    if ids:
        print(f"\nRendere {len(ids)} Clips … (danach im Dashboard unter 'Clips')")
        processor.wait_idle()
        for cid in ids:
            c = db.get_clip(cid)
            print(f"  #{cid} {c['status']:<8} {c.get('title') or ''}  {c.get('final_path') or c.get('error') or ''}")


def cmd_connect(cfg: dict, args) -> None:
    db = _db(cfg)
    if args.platform == "youtube":
        from .uploaders import youtube

        name, creds = youtube.connect(cfg, manual=args.manual)
        db.add_account("youtube", name, creds)
    elif args.platform == "tiktok":
        from .uploaders import tiktok

        print("1) Link öffnen und den TikTok-Account freigeben:\n\n" + tiktok.auth_url(cfg))
        print("\n2) Die komplette URL, auf die TikTok danach weiterleitet, hier einfügen:")
        name, creds = tiktok.connect(cfg, input("> "))
        db.add_account("tiktok", name, creds)
    else:
        from .uploaders import instagram

        token = args.token or input("Instagram Long-Lived Access Token: ").strip()
        name, creds = instagram.connect(cfg, token)
        db.add_account("instagram", name, creds)
    print(f"✓ {args.platform}-Account '{name}' verbunden")


def cmd_check(cfg: dict, _args) -> None:
    import subprocess

    ok = lambda b: "✓" if b else "✗"  # noqa: E731
    ff = ffmpeg_bin(cfg)
    has_ff = bool(shutil.which(ff) or Path(ff).exists())
    print(f"{ok(has_ff)} ffmpeg: {ff}")
    if has_ff:
        from .util import pick_encoder

        print(f"  Encoder: {pick_encoder(cfg)[1]}")
        filters = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        print(f"{ok(' ass ' in filters)} ffmpeg mit libass (Untertitel)")
    for mod in ("streamlink", "yt_dlp", "chat_downloader", "cv2", "faster_whisper"):
        try:
            __import__(mod)
            print(f"✓ {mod}")
        except ImportError:
            print(f"✗ {mod} fehlt (pip install -r requirements.txt)")
    if IS_MAC_ARM:
        try:
            __import__("mlx_whisper")
            print("✓ mlx_whisper (schnelle Transkription auf dem Mac)")
        except ImportError:
            print("○ mlx_whisper fehlt – empfohlen auf dem Mac: pip install mlx-whisper")
    t = cfg["transcription"]
    if t.get("whispercpp_bin"):
        print(f"{ok(Path(t['whispercpp_bin']).exists())} whisper.cpp: {t['whispercpp_bin']}")
    if cfg["llm"].get("enabled"):
        import requests

        try:
            tags = requests.get(cfg["llm"]["url"] + "/api/tags", timeout=5).json()
            names = [m["name"] for m in tags.get("models", [])]
            print(f"{ok(cfg['llm']['model'] in names)} Ollama erreichbar, Modell {cfg['llm']['model']} "
                  f"{'vorhanden' if cfg['llm']['model'] in names else 'fehlt: ollama pull ' + cfg['llm']['model']}")
        except Exception as exc:  # noqa: BLE001
            print(f"✗ Ollama nicht erreichbar ({exc})")
    else:
        print("○ Lokales LLM (Ollama) aus – Titel kommen aus Vorlagen")
    print(f"○ Daten-Ordner: {cfg['data_dir']}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m clipper", description="GTA Clipping-Dienst")
    p.add_argument("--config", "-c", help="Pfad zur config.yaml")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Dashboard + Überwachung starten (Standard)")
    a = sub.add_parser("add", help="Streamer hinzufügen")
    a.add_argument("url")
    a.add_argument("--name")
    a.add_argument("--language", help="z.B. de, en (Standard aus config)")
    sub.add_parser("list", help="Streamer und Accounts anzeigen")
    r = sub.add_parser("remove", help="Streamer entfernen")
    r.add_argument("name")
    v = sub.add_parser("vod", help="Vergangenen Stream (URL oder Datei) nachträglich clippen")
    v.add_argument("source", help="VOD-URL (Twitch/YouTube) oder lokale Videodatei")
    v.add_argument("--streamer", required=True, help="Name des Streamers (muss angelegt sein)")
    v.add_argument("--chat", help="Chat-Datei (JSONL/JSON), sonst automatisch")
    v.add_argument("--lag", type=float, help="Chat-Verzögerung in Sekunden (Standard 8)")
    c = sub.add_parser("connect", help="Upload-Account verbinden")
    c.add_argument("platform", choices=["youtube", "tiktok", "instagram"])
    c.add_argument("--manual", action="store_true", help="YouTube: Login ohne lokalen Browser")
    c.add_argument("--token", help="Instagram Access Token")
    sub.add_parser("check", help="Installation prüfen")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg["data_dir"])
    handlers = {"run": cmd_run, "add": cmd_add, "list": cmd_list, "remove": cmd_remove, "vod": cmd_vod,
                "connect": cmd_connect, "check": cmd_check, None: cmd_run}
    try:
        handlers[args.cmd](cfg, args)
    except KeyboardInterrupt:
        print("\nBeendet.")
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        sys.exit(f"Fehler: {exc}")
