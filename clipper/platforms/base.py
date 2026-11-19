"""Gemeinsame Typen und Live-Erkennung über streamlink."""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, field
from typing import Iterator

log = logging.getLogger("clipper.platform")


@dataclass
class LiveInfo:
    live: bool
    title: str | None = None
    category: str | None = None
    video_id: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ChatMessage:
    ts: float          # Unix-Zeit (live) oder Sekunden ab Videostart (VOD)
    author: str
    text: str
    color: str | None = None   # Namensfarbe des Chatters (#RRGGBB), fürs Chat-Overlay
    kind: str = "msg"          # msg | raid | gifts (Raids/Sub-Geschenkwellen lösen leere Chat-Ausbrüche aus)


class Platform:
    name = "base"

    def __init__(self, url: str, channel: str, cfg: dict):
        self.url = url
        self.channel = channel
        self.cfg = cfg

    # ------------------------------------------------------------------ live
    def live_url(self) -> str:
        return self.url

    def check_live(self) -> LiveInfo:
        return streamlink_check(self.live_url())

    # ------------------------------------------------------------------ capture
    def streamlink_args(self) -> list[str]:
        return []

    def capture_source_cmd(self, info: LiveInfo) -> list[str] | None:
        """Prozess, der den Live-Stream als MPEG-TS auf stdout schreibt (None = ffmpeg liest selbst)."""
        quality = self.cfg["monitor"]["quality"]
        if self.cfg["monitor"].get("capture_tool") == "yt-dlp":
            return [sys.executable, "-m", "yt_dlp", "--quiet", "--no-part", "-f", "best[height<=1080]/best",
                    "-o", "-", self.live_url()]
        return [sys.executable, "-m", "streamlink", "--stdout", "--loglevel", "warning",
                "--stream-segment-threads", "2", "--retry-open", "3", "--hls-live-edge", "3",
                *self.streamlink_args(), self.live_url(), quality]

    def ffmpeg_input_args(self, info: LiveInfo) -> list[str]:
        return ["-i", "pipe:0"]

    # ------------------------------------------------------------------ chat
    def chat_messages(self, info: LiveInfo, stop: threading.Event) -> Iterator[ChatMessage]:
        return iter(())

    def chat_send_time_is_wall_clock(self) -> bool:
        return True


def streamlink_check(url: str) -> LiveInfo:
    """Live-Status + Metadaten (Titel, Kategorie, ID) über das passende streamlink-Plugin."""
    from streamlink import Streamlink
    from streamlink.exceptions import NoPluginError, PluginError

    session = Streamlink()
    try:
        _name, plugin_cls, resolved = session.resolve_url(url)
    except NoPluginError:
        return LiveInfo(False, extra={"error": "Kein streamlink-Plugin für diese URL"})
    plugin = plugin_cls(session, resolved)
    try:
        streams = plugin.streams()
    except PluginError as exc:
        log.debug("streamlink %s: %s", url, exc)
        if "Unable to open URL" in str(exc):
            # Netzwerk-/Plattformfehler – nicht als "offline" tarnen, sondern im Dashboard anzeigen
            return LiveInfo(False, extra={"error": f"Plattform nicht erreichbar: {str(exc)[:160]}"})
        streams = {}
    try:
        title, category, vid = plugin.get_title(), plugin.get_category(), plugin.get_id()
    except Exception:  # noqa: BLE001
        title = category = vid = None
    return LiveInfo(bool(streams), title=title, category=category, video_id=vid)
