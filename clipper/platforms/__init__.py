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

__all__ = ["ChatMessage", "LiveInfo", "Platform", "PLATFORMS", "detect_platform", "handle_url", "make_platform"]


HANDLE_RE = re.compile(r"@?[\w.\-]{2,60}")
PREFIX_RE = re.compile(r"(twitch|kick|youtube|yt)\s*[:/]\s*(@?[\w.\-]{2,60})", re.IGNORECASE)


def handle_url(platform: str, handle: str) -> tuple[str, str, str]:
    """Handle + Plattform -> (plattform, kanalname, URL)."""
    handle = handle.strip().lstrip("@")
    if not HANDLE_RE.fullmatch(handle):
        raise ValueError(f"Ungültiger Handle: {handle!r}")
    if platform == "twitch":
        return "twitch", handle.lower(), f"https://www.twitch.tv/{handle.lower()}"
    if platform == "kick":
        return "kick", handle.lower(), f"https://kick.com/{handle.lower()}"
    if platform == "youtube":
        return "youtube", handle, f"https://www.youtube.com/@{handle}"
    raise ValueError(f"Unbekannte Plattform: {platform} (twitch, youtube oder kick)")


def detect_platform(url: str, platform: str | None = None) -> tuple[str, str, str]:
    """Link oder Handle -> (plattform, kanalname, normalisierte URL).

    Akzeptiert ganze Links, "twitch:name"/"kick:name"/"yt:@name" oder nur den Handle. Bei einem reinen Handle
    entscheidet `platform`; ohne Angabe gilt "@name" als YouTube, sonst Twitch.
    """
    url = url.strip()
    if url.startswith("file://"):
        name = re.sub(r"\.[^.]+$", "", url.rsplit("/", 1)[-1]) or "test"
        return "local", name, url
    m = PREFIX_RE.fullmatch(url)
    if m:
        pf = m.group(1).lower()
        return handle_url("youtube" if pf == "yt" else pf, m.group(2))
    if "/" not in url and not re.search(r"\.(tv|com|be)$", url.lower()) and HANDLE_RE.fullmatch(url):
        return handle_url(platform or ("youtube" if url.startswith("@") else "twitch"), url)
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
    raise ValueError(f"Unbekannter Link: {url} – Handle (z.B. papaplatte) oder Link von Twitch, YouTube oder Kick")


def make_platform(streamer: dict, cfg: dict) -> Platform:
    cls = PLATFORMS[streamer["platform"]]
    return cls(streamer["url"], streamer["channel"], cfg)
