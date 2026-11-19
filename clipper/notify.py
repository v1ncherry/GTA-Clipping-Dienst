"""Benachrichtigungen aufs Handy (Discord-Webhook und/oder Telegram-Bot) und Autopilot (automatisch hochladen)."""

from __future__ import annotations

import logging
import threading

import requests

from .config import effective_config
from .db import DB

log = logging.getLogger("clipper.notify")


def _targets(cfg: dict) -> list[tuple[str, str, dict]]:
    n = cfg.get("notify") or {}
    out = []
    if n.get("discord_webhook"):
        out.append(("Discord", n["discord_webhook"], {}))
    if n.get("telegram_token") and n.get("telegram_chat_id"):
        out.append(("Telegram", f"https://api.telegram.org/bot{n['telegram_token']}/sendMessage",
                    {"chat_id": n["telegram_chat_id"]}))
    return out


def send_sync(cfg: dict, text: str) -> list[str]:
    """Nachricht an alle eingerichteten Ziele schicken. Rückgabe: Fehlermeldungen (leer = alles ok)."""
    errors = []
    for name, url, extra in _targets(cfg):
        try:
            if name == "Discord":
                r = requests.post(url, json={"content": text[:1900], "username": "ClipStream"}, timeout=15)
            else:
                r = requests.post(url, data={**extra, "text": text[:4000], "disable_web_page_preview": "true"},
                                  timeout=15)
            if r.status_code >= 300:
                errors.append(f"{name}: HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as exc:
            errors.append(f"{name}: {exc}")
    for e in errors:
        log.warning("Benachrichtigung fehlgeschlagen – %s", e)
    return errors


def send(cfg: dict, text: str) -> None:
    if _targets(cfg):
        threading.Thread(target=send_sync, args=(cfg, text), daemon=True, name="notify").start()


class Autopilot:
    """Reagiert auf fertige Clips und Upload-Ergebnisse: benachrichtigen und ggf. automatisch hochladen."""

    def __init__(self, cfg: dict, db: DB, uploader, dashboard_url: str | None = None):
        self.cfg = cfg
        self.db = db
        self.uploader = uploader
        self.dashboard_url = dashboard_url

    def _link(self, cfg: dict) -> str:
        base = (cfg.get("notify") or {}).get("public_url") or self.dashboard_url or ""
        return f"\n{base.rstrip('/')}/#clips" if base else ""

    def clip_ready(self, clip_id: int, first: bool = True) -> None:
        clip = self.db.get_clip(clip_id)
        if not clip or not first:
            return
        streamer = self.db.get_streamer(clip["streamer_id"]) or {"name": "?"}
        cfg = effective_config(self.cfg, self.db, streamer)
        a, n = cfg.get("autopilot") or {}, cfg.get("notify") or {}
        score = float(clip.get("score") or 0)
        auto = False
        llm_ok = clip.get("llm_score") is None or float(clip["llm_score"]) >= float(a.get("min_llm_score", 0) or 0)
        if (a.get("enabled") and score >= float(a.get("min_score", 95)) and llm_ok and clip["source"] != "manual"
                and not clip.get("forced") and not self.db.list_uploads(clip_id)):
            ids = streamer.get("upload_targets") or [acc["id"] for acc in self.db.list_accounts()]
            if ids:
                try:
                    self.uploader.upload_clip(clip_id, ids)
                    auto = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("Autopilot-Upload für Clip %s nicht möglich: %s", clip_id, exc)
        if n.get("on_ready", True) and score >= float(n.get("min_score", 0)):
            extra = " – wird automatisch hochgeladen" if auto else ""
            send(cfg, f"🎬 Neuer Clip von {streamer['name']} (Score {score:.0f}){extra}\n{clip.get('title') or ''}"
                      f"{self._link(cfg)}")

    def upload_result(self, clip: dict, account: dict, ok: bool, detail: str) -> None:
        streamer = self.db.get_streamer(clip["streamer_id"]) or {"name": "?"}
        cfg = effective_config(self.cfg, self.db, streamer)
        if not (cfg.get("notify") or {}).get("on_upload", True):
            return
        head = "✅ Hochgeladen" if ok else "❌ Upload fehlgeschlagen"
        send(cfg, f"{head} – {account['platform']} · {account['name']}\n{clip.get('title') or ''}\n{detail}")
