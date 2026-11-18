"""Testplattform: eine lokale Videodatei wird wie ein Live-Stream abgespielt (Probelauf ohne echten Stream).

URL-Format: file:///pfad/zum/video.mp4  – optionaler Chat daneben: video.chat.jsonl
Jede Chat-Zeile: {"t": Sekunden ab Start, "author": "...", "text": "..."}
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse

from .base import ChatMessage, LiveInfo, Platform


def file_url_to_path(url: str) -> Path:
    if url.startswith("file://"):
        parsed = urlparse(url)
        path = unquote(parsed.path)
        if len(path) > 2 and path[0] == "/" and path[2] == ":":  # file:///C:/...
            path = path[1:]
        return Path(path)
    return Path(url)


def load_chat_file(path: Path) -> list[ChatMessage]:
    """Chat aus JSONL ({t, author, text}) oder chat-downloader-JSON (time_in_seconds, message, author.name)."""
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8").strip()
    items: list[dict] = []
    if raw.startswith("["):
        items = json.loads(raw)
    else:
        items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    out = []
    for it in items:
        t = it.get("t", it.get("time_in_seconds"))
        text = it.get("text", it.get("message"))
        author = it.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        if t is None or not text:
            continue
        out.append(ChatMessage(ts=float(t), author=str(author or "?"), text=str(text)))
    out.sort(key=lambda m: m.ts)
    return out


class Local(Platform):
    name = "local"

    def __init__(self, url: str, channel: str, cfg: dict):
        super().__init__(url, channel, cfg)
        self.path = file_url_to_path(url)
        self._played = False

    def check_live(self) -> LiveInfo:
        live = self.path.exists() and not self._played
        return LiveInfo(live, title=f"GTA 6 Testlauf ({self.path.name})", category="Grand Theft Auto VI")

    def capture_source_cmd(self, info: LiveInfo) -> list[str] | None:
        self._played = True
        return None

    def ffmpeg_input_args(self, info: LiveInfo) -> list[str]:
        return ["-re", "-i", str(self.path)]

    def chat_messages(self, info: LiveInfo, stop: threading.Event) -> Iterator[ChatMessage]:
        msgs = load_chat_file(self.path.with_suffix(".chat.jsonl"))
        start = time.time()
        for m in msgs:
            delay = start + m.ts - time.time()
            if delay > 0 and stop.wait(delay):
                return
            yield ChatMessage(ts=time.time(), author=m.author, text=m.text)
