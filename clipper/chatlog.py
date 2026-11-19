"""Chat pro Clip speichern (chat.json) – Grundlage für das Chat-Overlay im Video."""

from __future__ import annotations

import json
from pathlib import Path

from .signals import Timeline

# Reaktionen erscheinen im Overlay so viele Sekunden nach dem Moment (wie für Zuschauer im Stream)
OVERLAY_REACTION = 2.0


def save_clip_chat(path: Path, timeline: Timeline, start: float, end: float, lag: float, base: float) -> int:
    """Nachrichten rund um den Clip [start, end] (Inhalts-Zeit) speichern; Zeiten relativ zum Rohvideo (base)."""
    shift = lag - OVERLAY_REACTION
    out = []
    for m in timeline.messages_between(start + shift - 15, end + shift + 2):
        t, author, text = m[0], m[1], m[2]
        color = m[3] if len(m) > 3 else None
        out.append({"t": round(t - shift - base, 2), "author": author, "text": text, "color": color})
    path.write_text(json.dumps({"messages": out}, ensure_ascii=False), encoding="utf-8")
    return len(out)


def load_clip_chat(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("messages", [])
    except (OSError, ValueError):
        return []
