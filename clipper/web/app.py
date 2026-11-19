"""Web-Dashboard (FastAPI): Live-Übersicht, Clip-Queue mit 1-Klick-Upload, Streamer- und Account-Verwaltung."""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import data_path, deep_merge, effective_config
from ..db import DB
from .. import learn
from ..platforms import detect_platform
from ..processing import discard_clip
from ..uploaders import instagram, tiktok
from ..util import ffmpeg_bin, run, slugify

log = logging.getLogger("clipper.web")
STATIC = Path(__file__).parent / "static"


DESKTOP_COOKIE = "clipper_desktop"


def desktop_token(cfg: dict) -> str:
    """Geheimer Schlüssel für das Programmfenster (desktop.py), damit es ohne Passwortabfrage reinkommt.

    Liegt in data/secrets – wer diese Datei lesen kann, kann auch config.yaml (mit dem Passwort) lesen.
    """
    path = data_path(cfg, "secrets") / "desktop.token"
    try:
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def create_app(cfg: dict, db: DB, orchestrator, processor, uploader, started: float | None = None,
               lan_url: str | None = None) -> FastAPI:
    app = FastAPI(title="ClipStream", docs_url=None, redoc_url=None)
    password = str(cfg["web"].get("password") or "")
    app_token = desktop_token(cfg)
    started = started or time.time()

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if request.url.path == "/desktop-login":
            if secrets.compare_digest(request.query_params.get("token", ""), app_token):
                resp = RedirectResponse("/", status_code=303)
                resp.set_cookie(DESKTOP_COOKIE, app_token, httponly=True, samesite="strict")
                return resp
            return Response("Ungültiger Schlüssel", status_code=403)
        if password and not secrets.compare_digest(request.cookies.get(DESKTOP_COOKIE, ""), app_token):
            ok = False
            header = request.headers.get("authorization", "")
            if header.lower().startswith("basic "):
                try:
                    _user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                    ok = secrets.compare_digest(pw, password)
                except (binascii.Error, UnicodeDecodeError):
                    ok = False
            if not ok:
                return Response("Login erforderlich", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="ClipStream"'})
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(STATIC / "icon.ico")

    # ------------------------------------------------------------------ Status
    @app.get("/api/status")
    def status():
        entries = orchestrator.status()
        by_id = {s["id"]: s for s in db.list_streamers()}
        default_trigger = effective_config(cfg, db)["detection"]["trigger_score"]
        for e in entries:  # Auslöse-Schwelle pro Streamer für die Linie in der Hype-Kurve
            det = ((by_id.get(e["id"]) or {}).get("settings") or {}).get("detection") or {}
            e["trigger"] = det.get("trigger_score") or default_trigger
        return {"streamers": entries, "processing": processor.current, "queue": processor.q.qsize(),
                "time": time.time(), "clips": db.count_clips_by_status(), "accounts": len(db.list_accounts())}

    @app.get("/api/info")
    def info():
        host = cfg["web"]["host"]
        return {"version": __version__, "started": started, "password": bool(password),
                "lan_url": lan_url if host in ("0.0.0.0", "::") else None}

    @app.get("/api/streamers/{sid}/history")
    def history(sid: int):
        sess = orchestrator.session(sid)
        return {"points": sess.history() if sess else []}

    # ------------------------------------------------------------------ Streamer
    @app.get("/api/streamers")
    def list_streamers():
        return [{**s, "learned": learn.last_note(db, s["id"])} for s in db.list_streamers()]

    @app.post("/api/streamers")
    def add_streamer(payload: dict = Body(...)):
        try:
            pf = payload.get("platform") if payload.get("platform") in ("twitch", "youtube", "kick") else None
            platform, channel, url = detect_platform(payload.get("url") or payload.get("handle") or "", pf)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        name = (payload.get("name") or channel).strip()
        if db.get_streamer_by_name(name):
            raise HTTPException(400, f"Streamer '{name}' existiert schon")
        settings = {}
        if payload.get("language"):
            settings["transcription"] = {"language": payload["language"]}
        sid = db.add_streamer(name, url, platform, channel, settings)
        return db.get_streamer(sid)

    @app.patch("/api/streamers/{sid}")
    def update_streamer(sid: int, payload: dict = Body(...)):
        st = _streamer(sid)
        fields = {}
        if "enabled" in payload:
            fields["enabled"] = 1 if payload["enabled"] else 0
        if payload.get("name"):
            fields["name"] = payload["name"].strip()
        if "upload_targets" in payload:
            fields["upload_targets"] = [int(a) for a in payload["upload_targets"]]
        if "settings" in payload and isinstance(payload["settings"], dict):
            fields["settings"] = _merge_settings(st.get("settings") or {}, payload["settings"])
        db.update_streamer(sid, **fields)
        return db.get_streamer(sid)

    @app.delete("/api/streamers/{sid}")
    def delete_streamer(sid: int):
        _streamer(sid)
        db.delete_streamer(sid)
        return {"ok": True}

    @app.post("/api/streamers/{sid}/check")
    def check_now(sid: int):
        orchestrator.check_now(sid)
        return {"ok": True}

    @app.post("/api/streamers/{sid}/clip")
    def clip_now(sid: int, payload: dict = Body(default={})):
        sess = orchestrator.session(sid)
        if not sess:
            raise HTTPException(400, "Streamer ist gerade nicht live")
        try:
            sess.manual_clip(float(payload.get("seconds", 45)))
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/streamers/{sid}/snapshot")
    def snapshot(sid: int):
        st = _streamer(sid)
        path = orchestrator.snapshot_path(sid)
        if path:
            return FileResponse(path, headers={"Cache-Control": "no-store"})
        for clip in db.list_clips(status="all", streamer_id=sid, limit=20):
            raw = clip.get("raw_path")
            if raw and Path(raw).exists():
                out = data_path(cfg, "buffer", f"calib-{slugify(st['name'])}.jpg")
                run([ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss",
                     str(clip["raw_start"] or 0), "-i", raw, "-frames:v", "1", "-vf", "scale=960:-2", str(out)],
                    timeout=60, check=False)
                if out.exists():
                    return FileResponse(out, headers={"Cache-Control": "no-store"})
        raise HTTPException(404, "Kein Bild verfügbar – Streamer muss live sein oder es muss schon einen Clip geben")

    @app.put("/api/streamers/{sid}/facecam")
    def set_facecam(sid: int, payload: dict = Body(...)):
        _streamer(sid)
        mode = payload.get("mode", "auto")
        if mode == "rect":
            r = payload.get("rect") or {}
            try:
                value = {k: max(0.0, min(1.0, float(r[k]))) for k in ("x", "y", "w", "h")}
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, "rect braucht x, y, w, h (0-1)") from exc
        elif mode == "none":
            value = "none"
        else:
            value = None
        db.update_streamer(sid, facecam=value)
        return db.get_streamer(sid)

    # ------------------------------------------------------------------ Clips
    def _clip_out(c: dict) -> dict:
        c = dict(c)
        c.pop("words", None)
        stamp = int(Path(c["final_path"]).stat().st_mtime) if c.get("final_path") and Path(c["final_path"]).exists() else 0
        c["video_url"] = f"/media/clip/{c['id']}/final.mp4?v={stamp}" if stamp else None
        c["thumb_url"] = f"/media/clip/{c['id']}/thumb.jpg?v={stamp}" if stamp else None
        lp = c.pop("long_path", None)
        c["long_url"] = f"/media/clip/{c['id']}/long.mp4?v={stamp}" if lp and Path(lp).exists() else None
        c["uploads"] = db.list_uploads(c["id"])
        c["step"] = processor.current.get(c["id"])
        for k in ("raw_path", "final_path", "thumb_path", "dir"):
            c.pop(k, None)
        return c

    @app.get("/api/clips")
    def list_clips(status: str = "open", streamer_id: int | None = None, limit: int = 60):
        return [_clip_out(c) for c in db.list_clips(status=status, streamer_id=streamer_id, limit=limit)]

    @app.get("/api/clips/{cid}")
    def get_clip(cid: int):
        return _clip_out(_clip(cid))

    @app.patch("/api/clips/{cid}")
    def update_clip(cid: int, payload: dict = Body(...)):
        _clip(cid)
        fields = {k: payload[k] for k in ("title", "hook", "caption") if isinstance(payload.get(k), str)}
        if isinstance(payload.get("hashtags"), list):
            fields["hashtags"] = [str(h) for h in payload["hashtags"]]
        db.update_clip(cid, **fields)
        return _clip_out(_clip(cid))

    @app.post("/api/clips/{cid}/rerender")
    def rerender(cid: int, payload: dict = Body(default={})):
        """Neu rendern mit anderem Layout – bei aussortierten Clips: trotzdem rendern."""
        c = _clip(cid)
        opts = dict(c.get("render_opts") or {})
        if payload.get("layout") in LAYOUTS:
            opts["layout"] = payload["layout"]
        if payload.get("cam_size") in ("s", "m", "l"):
            opts["cam_size"] = payload["cam_size"]
        if isinstance(payload.get("chat"), bool):
            opts["chat"] = payload["chat"]
        fields = {"render_opts": opts, "layout_override": None}
        if c["status"] == "filtered":
            fields["forced"] = 1
        db.update_clip(cid, **fields)
        processor.enqueue(cid, rerender=True)
        return {"ok": True}

    # ------------------------------------------------------------------ Einstellungen
    @app.get("/api/settings")
    def get_settings():
        eff = effective_config(cfg, db)
        return {key: (bool(_pick(eff, key)) if GLOBAL_SETTINGS[key][0] == "secret" else _pick(eff, key))
                for key in GLOBAL_SETTINGS}

    @app.post("/api/notify/test")
    def notify_test():
        from ..notify import _targets, send_sync

        eff = effective_config(cfg, db)
        if not _targets(eff):
            raise HTTPException(400, "Noch kein Discord-Webhook oder Telegram-Bot eingetragen")
        errors = send_sync(eff, "👋 Test von ClipStream – Benachrichtigungen funktionieren.")
        if errors:
            raise HTTPException(400, "; ".join(errors))
        return {"ok": True}

    @app.put("/api/settings")
    def put_settings(payload: dict = Body(...)):
        current = db.get_setting("global") or {}
        try:
            update = validate_global_settings(payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        db.set_setting("global", deep_merge(current, update))
        return get_settings()

    @app.post("/api/clips/{cid}/upload")
    def upload(cid: int, payload: dict = Body(...)):
        _clip(cid)
        ids = [int(a) for a in payload.get("account_ids", [])]
        if not ids:
            raise HTTPException(400, "Keine Accounts ausgewählt")
        try:
            return {"uploads": uploader.upload_clip(cid, ids)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/clips/{cid}")
    def delete_clip(cid: int, reason: str | None = None):
        c = _clip(cid)
        note = None
        if c["status"] != "filtered":   # Verwerfen = schlechtes Feedback (lernt pro Streamer mit)
            note = learn.record(cfg, db, c, "bad", reason)
        discard_clip(db, cid)
        return {"ok": True, "learned": note}

    @app.get("/media/clip/{cid}/{name}")
    def media(cid: int, name: str):
        c = _clip(cid)
        path = {"final.mp4": c.get("final_path"), "thumb.jpg": c.get("thumb_path"),
                "long.mp4": c.get("long_path")}.get(name)
        if not path or not Path(path).exists():
            raise HTTPException(404)
        return FileResponse(path, filename=f"clip-{cid}.mp4" if name.endswith(".mp4") else None)

    # ------------------------------------------------------------------ Accounts
    @app.get("/api/accounts")
    def accounts():
        return [{"id": a["id"], "platform": a["platform"], "name": a["name"], "created_at": a["created_at"]}
                for a in db.list_accounts()]

    @app.delete("/api/accounts/{aid}")
    def delete_account(aid: int):
        db.delete_account(aid)
        return {"ok": True}

    @app.post("/api/accounts/instagram")
    def add_instagram(payload: dict = Body(...)):
        try:
            name, creds = instagram.connect(cfg, (payload.get("token") or "").strip())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc)) from exc
        return {"id": db.add_account("instagram", name, creds), "name": name}

    @app.get("/api/accounts/tiktok/auth-url")
    def tiktok_auth_url():
        try:
            return {"url": tiktok.auth_url(cfg)}
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/accounts/tiktok")
    def add_tiktok(payload: dict = Body(...)):
        try:
            name, creds = tiktok.connect(cfg, payload.get("redirected_url") or "")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc)) from exc
        return {"id": db.add_account("tiktok", name, creds), "name": name}

    @app.exception_handler(Exception)
    async def errors(_request: Request, exc: Exception):
        log.exception("API-Fehler")
        return JSONResponse({"detail": str(exc)}, status_code=500)

    # ------------------------------------------------------------------ helpers
    def _streamer(sid: int) -> dict:
        st = db.get_streamer(sid)
        if not st:
            raise HTTPException(404, "Streamer nicht gefunden")
        return st

    def _clip(cid: int) -> dict:
        c = db.get_clip(cid)
        if not c:
            raise HTTPException(404, "Clip nicht gefunden")
        return c

    return app


LAYOUTS = ("auto", "stack", "stack_chat", "crop", "crop_chat", "blur")

ALLOWED_SETTINGS = {
    "detection": {"trigger_score", "release_score", "max_clips_per_hour", "cooldown_seconds", "stream_share"},
    "clips": {"min_seconds", "max_seconds", "pre_roll", "post_roll"},
    "render": {"layout", "cam_size", "chat_overlay"},
    "transcription": {"language"},
    "monitor": {"only_keywords"},
}

# Was im Dashboard unter "Einstellungen" geändert werden darf: Pfad -> (Typ, Minimum, Maximum / erlaubte Werte)
GLOBAL_SETTINGS = {
    "detection.stream_share": ("num", 0, 100),
    "detection.trigger_score": ("num", 40, 99),
    "detection.max_clips_per_hour": ("num", 1, 60),
    "detection.weights": ("weights", 0, 1),
    "filters.enabled": ("bool",),
    "filters.min_hype_ratio": ("num", 0, 1),
    "filters.max_answer_ratio": ("num", 0.1, 1),
    "filters.min_stream_reaction": ("num", 0, 5),
    "filters.question_check": ("bool",),
    "special.enabled": ("bool",),
    "special.categories": ("choices", ("easteregg", "glitch", "stichwort")),
    "special.keywords": ("phrases",),
    "special.silence": ("bool",),
    "monitor.only_keywords": ("words",),
    "render.layout": ("choice", LAYOUTS),
    "render.cam_size": ("choice", ("s", "m", "l")),
    "render.chat_overlay": ("bool",),
    "render.hook_text": ("bool",),
    "render.tiktok_long": ("bool",),
    "learn.enabled": ("bool",),
    "autopilot.enabled": ("bool",),
    "autopilot.min_score": ("num", 70, 100),
    "notify.discord_webhook": ("secret", "https://"),
    "notify.telegram_token": ("secret", ""),
    "notify.telegram_chat_id": ("text",),
    "notify.min_score": ("num", 0, 100),
    "notify.on_ready": ("bool",),
    "notify.on_upload": ("bool",),
    "notify.public_url": ("text",),
    "clips.max_seconds": ("num", 15, 180),
    "hashtags": ("words",),
}


def _pick(cfg: dict, path: str):
    node = cfg
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def validate_global_settings(payload: dict) -> dict:
    """Flache Pfade ({"detection.stream_share": 40}) prüfen und in verschachtelte Overrides umwandeln."""
    out: dict = {}
    for path, value in payload.items():
        spec = GLOBAL_SETTINGS.get(path)
        if spec is None:
            raise ValueError(f"Unbekannte Einstellung: {path}")
        kind = spec[0]
        if kind == "num":
            value = float(value)
            value = min(max(value, spec[1]), spec[2])
            value = int(value) if float(value).is_integer() else round(value, 3)
        elif kind == "bool":
            value = bool(value)
        elif kind == "choice":
            if value not in spec[1]:
                raise ValueError(f"{path}: ungültiger Wert {value!r}")
        elif kind == "choices":
            value = [v for v in value if v in spec[1]]
        elif kind == "weights":
            value = {k: min(max(float(v), 0.0), 1.0) for k, v in dict(value).items()
                     if k in ("chat_rate", "chatters", "emotes", "audio", "motion")}
        elif kind == "secret":   # wird nie zurückgegeben, nur "gesetzt ja/nein"
            value = str(value or "").strip()
            if value and spec[1] and not value.startswith(spec[1]):
                raise ValueError(f"{path}: muss mit {spec[1]} beginnen")
        elif kind == "text":
            value = str(value or "").strip()[:300]
        elif kind == "phrases":   # durch Komma getrennt, Leerzeichen erlaubt ("strip club")
            if isinstance(value, str):
                value = value.split(",")
            value = [str(w).strip().lower() for w in value if str(w).strip()][:50]
        elif kind == "words":
            if isinstance(value, str):
                value = [w.strip() for w in value.replace(",", " ").split() if w.strip()]
            value = [str(w).strip() for w in value if str(w).strip()][:30]
        node = out
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _merge_settings(current: dict, update: dict) -> dict:
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in current.items()}
    for section, values in update.items():
        if section not in ALLOWED_SETTINGS or not isinstance(values, dict):
            continue
        sec = out.setdefault(section, {})
        for key, val in values.items():
            if key not in ALLOWED_SETTINGS[section]:
                continue
            if val is None or val == "":
                sec.pop(key, None)
            else:
                sec[key] = val
        if not sec:
            out.pop(section, None)
    return out
