"""Plattform-Erkennung aus der URL."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import ChatMessage, LiveInfo, Platform
from .kick import Kick
from .local import Local
from .twitch import Twitch
from .youtube import YouTube

PLATFORMS = {"twitch": Twitch, "youtube": YouTube, "kick": Kick, "local": Local}

__all__ = ["ChatMessage", "LiveInfo", "Platform", "PLATFORMS", "detect_platform", "make_platform"]


def detect_platform(url: str) -> tuple[str, str, str]:
    """URL -> (plattform, kanalname, normalisierte URL). Wirft ValueError bei unbekannten URLs."""
    url = url.strip()
    if url.startswith("file://"):
        name = re.sub(r"\.[^.]+$", "", url.rsplit("/", 1)[-1]) or "test"
        return "local", name, url
    if not re.match(r"^https?://", url):
        url = "https://" + url
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("twitch.tv") and parts:
        chan = parts[0].lower()
        return "twitch", chan, f"https://www.twitch.tv/{chan}"
    if host.endswith("kick.com") and parts:
        chan = parts[0].lower()
        return "kick", chan, f"https://kick.com/{chan}"
    if host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com"):
        if parts and parts[0].startswith("@"):
            return "youtube", parts[0][1:], f"https://www.youtube.com/{parts[0]}"
        if len(parts) >= 2 and parts[0] in ("channel", "c", "user"):
            return "youtube", parts[1], f"https://www.youtube.com/{parts[0]}/{parts[1]}"
        if parts and parts[0] in ("watch", "live") or host == "youtu.be":
            return "youtube", parts[-1] if parts else "video", url
        if parts:
            return "youtube", parts[0], f"https://www.youtube.com/@{parts[0]}"
    raise ValueError(f"Unbekannte Stream-URL: {url} (unterstützt: twitch.tv, youtube.com, kick.com)")


def make_platform(streamer: dict, cfg: dict) -> Platform:
    cls = PLATFORMS[streamer["platform"]]
    return cls(streamer["url"], streamer["channel"], cfg)
