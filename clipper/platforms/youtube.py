"""YouTube Live: Live-Check über streamlink (/live-URL des Kanals), Chat über chat-downloader."""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterator

from .base import ChatMessage, LiveInfo, Platform, streamlink_check

log = logging.getLogger("clipper.youtube")


class YouTube(Platform):
    name = "youtube"

    def live_url(self) -> str:
        if "watch?v=" in self.url or "/live/" in self.url or self.url.rstrip("/").endswith("/live"):
            return self.url
        return self.url.rstrip("/") + "/live"

    def check_live(self) -> LiveInfo:
        info = streamlink_check(self.live_url())
        if info.live and not info.video_id:
            # Fallback: Video-ID über yt-dlp ermitteln (wird für den Chat gebraucht)
            try:
                import yt_dlp

                with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
                    meta = ydl.extract_info(self.live_url(), download=False)
                info.video_id = meta.get("id")
                info.title = info.title or meta.get("title")
            except Exception as exc:  # noqa: BLE001
                log.debug("yt-dlp Fallback fehlgeschlagen: %s", exc)
        return info

    def chat_messages(self, info: LiveInfo, stop: threading.Event) -> Iterator[ChatMessage]:
        if not info.video_id:
            log.warning("YouTube: keine Video-ID, Chat wird nicht gelesen")
            return
        from chat_downloader import ChatDownloader

        url = f"https://www.youtube.com/watch?v={info.video_id}"
        backoff = 5.0
        while not stop.is_set():
            try:
                chat = ChatDownloader().get_chat(url, message_groups=["messages", "superchat"])
                log.info("YouTube-Chat verbunden: %s", info.video_id)
                for item in chat:
                    if stop.is_set():
                        return
                    text = item.get("message") or ""
                    if not text:
                        continue
                    ts = item.get("timestamp")
                    ts = ts / 1_000_000 if ts else time.time()
                    if abs(ts - time.time()) > 120:
                        ts = time.time()
                    author = (item.get("author") or {}).get("name") or "?"
                    yield ChatMessage(ts=ts, author=author, text=text)
                return  # Chat beendet (Stream vorbei)
            except Exception as exc:  # noqa: BLE001
                if stop.is_set():
                    return
                log.warning("YouTube-Chat Fehler (%s), neuer Versuch in %.0fs", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 120)
