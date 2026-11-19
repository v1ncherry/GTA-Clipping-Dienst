"""Upload-Verwaltung: ein Klick im Dashboard lädt einen Clip auf alle gewählten Accounts hoch."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..db import DB
from . import instagram, tiktok, youtube

log = logging.getLogger("clipper.upload")

PLATFORMS = ("youtube", "tiktok", "instagram")


def texts_for(platform: str, clip: dict) -> dict:
    title = clip.get("title") or "Stream-Highlight"
    tags = clip.get("hashtags") or []
    caption = clip.get("caption") or f"{title}\n\n{' '.join(tags)}"
    if platform == "youtube":
        desc = caption if "#shorts" in caption.lower() else caption + " #shorts"
        return {"title": title, "description": desc, "tags": [t.lstrip("#") for t in tags]}
    return {"caption": caption}


class UploadManager:
    def __init__(self, cfg: dict, db: DB):
        self.cfg = cfg
        self.db = db
        self.lock = threading.Lock()
        self.on_result = None   # Callback(clip, account, ok, detail) für Benachrichtigungen

    def upload_clip(self, clip_id: int, account_ids: list[int]) -> list[int]:
        clip = self.db.get_clip(clip_id)
        if not clip or not clip.get("final_path"):
            raise ValueError("Clip ist noch nicht fertig gerendert")
        ids = []
        for aid in account_ids:
            if not self.db.get_account(aid):
                continue
            uid = self.db.add_upload(clip_id, aid)
            ids.append(uid)
            threading.Thread(target=self._run, args=(clip_id, aid, uid), daemon=True, name=f"upload-{uid}").start()
        return ids

    def _run(self, clip_id: int, account_id: int, upload_id: int) -> None:
        clip = self.db.get_clip(clip_id)
        account = self.db.get_account(account_id)
        if not clip or not account:
            return
        self.db.update_upload(upload_id, status="uploading")
        try:
            path = clip["final_path"]
            t = texts_for(account["platform"], clip)
            if account["platform"] == "youtube":
                url = youtube.upload(self.cfg, account, self.db, path, t["title"], t["description"], t["tags"])
            elif account["platform"] == "tiktok":
                long_path = clip.get("long_path")
                url = tiktok.upload(self.cfg, account, self.db,
                                    long_path if long_path and Path(long_path).exists() else path, t["caption"])
            elif account["platform"] == "instagram":
                url = instagram.upload(self.cfg, account, self.db, path, t["caption"])
            else:
                raise ValueError(f"Unbekannte Plattform {account['platform']}")
            self.db.update_upload(upload_id, status="done", url=url)
            self.db.update_clip(clip_id, status="uploaded")
            try:
                from ..learn import record

                record(self.cfg, self.db, clip, "good")
            except Exception:  # noqa: BLE001
                log.exception("Feedback konnte nicht gespeichert werden")
            log.info("Clip %s hochgeladen auf %s (%s): %s", clip_id, account["platform"], account["name"], url)
            if self.on_result:
                self.on_result(clip, account, True, url)
        except Exception as exc:  # noqa: BLE001
            log.exception("Upload Clip %s -> %s fehlgeschlagen", clip_id, account["name"])
            self.db.update_upload(upload_id, status="failed", error=str(exc)[:1000])
            if self.on_result:
                self.on_result(clip, account, False, str(exc)[:300])
