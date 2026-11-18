"""Überwacht alle Streamer: prüft regelmäßig, ob sie live sind, und startet/stoppt Live-Sessions."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import streamer_config
from .db import DB
from .platforms import Platform, make_platform
from .session import LiveSession, cleanup_buffer, matches_keywords

log = logging.getLogger("clipper.monitor")


class Orchestrator:
    def __init__(self, cfg: dict, db: DB, processor):
        self.cfg = cfg
        self.db = db
        self.processor = processor
        self.sessions: dict[int, LiveSession] = {}
        self.states: dict[int, dict] = {}
        self.platforms: dict[int, tuple[str, Platform]] = {}
        self.checking: set[int] = set()
        self.last_check: dict[int, float] = {}
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="livecheck")
        self.lock = threading.RLock()
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        try:  # Reste eines Absturzes (Ringpuffer ohne Session) wegräumen, bevor neue Sessions starten
            cleanup_buffer(self.cfg)
        except Exception:  # noqa: BLE001
            log.exception("Aufräumen des Puffers fehlgeschlagen")
        self.thread = threading.Thread(target=self._loop, name="orchestrator", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        with self.lock:
            sessions = list(self.sessions.values())
        for s in sessions:
            s.stop()

    def _platform(self, streamer: dict) -> Platform:
        key = streamer["url"]
        cached = self.platforms.get(streamer["id"])
        if cached is None or cached[0] != key:
            cached = (key, make_platform(streamer, self.cfg))
            self.platforms[streamer["id"]] = cached
        return cached[1]

    def _loop(self) -> None:
        while not self.stop_evt.wait(3):
            try:
                streamers = {s["id"]: s for s in self.db.list_streamers()}
                with self.lock:
                    for sid, sess in list(self.sessions.items()):
                        st = streamers.get(sid)
                        if st is None or not st["enabled"]:
                            log.info("Streamer %s deaktiviert/entfernt – Session wird gestoppt", sess.streamer["name"])
                            sess.stop()
                for sid, st in streamers.items():
                    if not st["enabled"]:
                        self.states[sid] = {"status": "disabled"}
                        continue
                    with self.lock:
                        busy = sid in self.sessions or sid in self.checking
                    poll = streamer_config(self.cfg, st)["monitor"]["poll_seconds"]
                    if not busy and time.time() - self.last_check.get(sid, 0) >= poll:
                        with self.lock:
                            self.checking.add(sid)
                        self.pool.submit(self._check, st)
            except Exception:  # noqa: BLE001
                log.exception("Fehler in der Überwachung")

    def check_now(self, sid: int) -> None:
        self.last_check.pop(sid, None)

    def _check(self, streamer: dict) -> None:
        sid = streamer["id"]
        try:
            platform = self._platform(streamer)
            info = platform.check_live()
            scfg = streamer_config(self.cfg, streamer)
            state = {"status": "offline", "title": info.title, "category": info.category, "checked": time.time()}
            if info.live:
                if matches_keywords(info, scfg["monitor"].get("only_keywords") or []):
                    state["status"] = "live"
                    self._start_session(streamer, platform, info)
                else:
                    state["status"] = "live_other"
            if info.extra.get("error"):
                state.update(status="error", error=info.extra["error"])
            self.states[sid] = state
        except Exception as exc:  # noqa: BLE001
            log.warning("Live-Check %s fehlgeschlagen: %s", streamer["name"], exc)
            self.states[sid] = {"status": "error", "error": str(exc)[:300], "checked": time.time()}
        finally:
            self.last_check[sid] = time.time()
            with self.lock:
                self.checking.discard(sid)

    def _start_session(self, streamer: dict, platform: Platform, info) -> None:
        with self.lock:
            if streamer["id"] in self.sessions:
                return
            sess = LiveSession(streamer, platform, info, self.cfg, self.db, self.processor, on_end=self._ended)
            self.sessions[streamer["id"]] = sess
        sess.start()

    def _ended(self, sess: LiveSession) -> None:
        with self.lock:
            if self.sessions.get(sess.streamer["id"]) is sess:
                del self.sessions[sess.streamer["id"]]
        self.last_check[sess.streamer["id"]] = time.time()
        self.states[sess.streamer["id"]] = {"status": "offline", "checked": time.time()}

    # ------------------------------------------------------------------ für das Dashboard
    def status(self) -> list[dict]:
        out = []
        since = time.time() - 86400
        for st in self.db.list_streamers():
            sid = st["id"]
            entry = {"id": sid, "name": st["name"], "platform": st["platform"], "url": st["url"],
                     "enabled": bool(st["enabled"]), "clips_24h": self.db.count_clips_since(sid, since)}
            entry.update(self.states.get(sid, {"status": "checking" if st["enabled"] else "disabled"}))
            with self.lock:
                sess = self.sessions.get(sid)
            if sess:
                entry.update(sess.status())
                entry["status"] = "paused" if sess.paused else "live"
            out.append(entry)
        return out

    def session(self, sid: int) -> LiveSession | None:
        with self.lock:
            return self.sessions.get(sid)

    def snapshot_path(self, sid: int) -> Path | None:
        sess = self.session(sid)
        if sess and sess.snapshot.exists():
            return sess.snapshot
        return None
