"""Web-Dashboard (FastAPI): Live-Übersicht, Clip-Queue mit 1-Klick-Upload, Streamer- und Account-Verwaltung."""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..config import data_path
from ..db import DB
from ..platforms import detect_platform
from ..processing import discard_clip
from ..uploaders import instagram, tiktok
from ..util import ffmpeg_bin, run, slugify

log = logging.getLogger("clipper.web")
STATIC = Path(__file__).parent / "static"


def create_app(cfg: dict, db: DB, orchestrator, processor, uploader) -> FastAPI:
    app = FastAPI(title="GTA Clipper", docs_url=None, redoc_url=None)
    password = str(cfg["web"].get("password") or "")

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if password:
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
                                headers={"WWW-Authenticate": 'Basic realm="GTA Clipper"'})
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    # ------------------------------------------------------------------ Status
    @app.get("/api/status")
    def status():
        return {"streamers": orchestrator.status(), "processing": processor.current,
                "queue": processor.q.qsize(), "time": time.time()}

    @app.get("/api/streamers/{sid}/history")
    def history(sid: int):
        sess = orchestrator.session(sid)
        return {"points": sess.history() if sess else []}

    # ------------------------------------------------------------------ Streamer
    @app.get("/api/streamers")
    def list_streamers():
        return db.list_streamers()

    @app.post("/api/streamers")
    def add_streamer(payload: dict = Body(...)):
        try:
            platform, channel, url = detect_platform(payload.get("url", ""))
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
        _clip(cid)
        layout = payload.get("layout")
        db.update_clip(cid, layout_override=layout if layout in ("stack", "crop", "blur") else None)
        processor.enqueue(cid, rerender=True)
        return {"ok": True}

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
    def delete_clip(cid: int):
        _clip(cid)
        discard_clip(db, cid)
        return {"ok": True}

    @app.get("/media/clip/{cid}/{name}")
    def media(cid: int, name: str):
        c = _clip(cid)
        path = {"final.mp4": c.get("final_path"), "thumb.jpg": c.get("thumb_path")}.get(name)
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


ALLOWED_SETTINGS = {
    "detection": {"trigger_score", "release_score", "max_clips_per_hour", "cooldown_seconds"},
    "clips": {"min_seconds", "max_seconds", "pre_roll", "post_roll"},
    "render": {"layout"},
    "transcription": {"language"},
    "monitor": {"only_keywords"},
}


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
