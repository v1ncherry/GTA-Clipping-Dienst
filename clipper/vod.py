"""VOD-Modus: ein vergangenes Stream-Video (+ Chat-Replay) nachträglich analysieren und clippen.

Praktisch, um Einstellungen an alten Streams zu testen oder verpasste Streams nachzuholen.
Chat-Replay funktioniert für Twitch-VODs und YouTube-Livestream-Aufzeichnungen.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from .chatlog import save_clip_chat
from .config import data_path, effective_config, keyword_spec
from .db import DB
from .detector import Detector
from .platforms.base import ChatMessage
from .platforms.local import file_url_to_path, load_chat_file
from .signals import MediaAnalyzer, Timeline
from .util import probe, slugify

log = logging.getLogger("clipper.vod")


def download_vod(cfg: dict, url: str) -> Path:
    import yt_dlp

    out_dir = data_path(cfg, "vods")
    opts = {"format": "bv*[height<=1080]+ba/b[height<=1080]/b", "merge_output_format": "mp4",
            "outtmpl": str(out_dir / "%(extractor)s-%(id)s.%(ext)s"), "quiet": True, "noprogress": False}
    with yt_dlp.YoutubeDL(opts) as ydl:
        meta = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(meta)).with_suffix(".mp4")
    if not path.exists():
        cands = sorted(out_dir.glob(f"*{meta.get('id')}*"), key=lambda p: p.stat().st_mtime)
        path = cands[-1]
    return path


def download_chat(url: str, out: Path) -> list[ChatMessage]:
    from chat_downloader import ChatDownloader

    msgs, raw = [], []
    try:
        for item in ChatDownloader().get_chat(url, message_groups=["messages"]):
            t = item.get("time_in_seconds")
            text = item.get("message")
            if t is None or not text:
                continue
            author = (item.get("author") or {}).get("name") or "?"
            msgs.append(ChatMessage(float(t), author, text))
            raw.append({"t": float(t), "author": author, "text": text})
    except Exception as exc:  # noqa: BLE001
        log.warning("Chat-Replay nicht verfügbar (%s) – analysiere nur Bild & Ton", exc)
    if raw:
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in raw), encoding="utf-8")
    return msgs


def run_vod(cfg: dict, db: DB, processor, source: str, streamer: dict, chat_file: str | None = None,
            lag: float | None = None) -> list[int]:
    scfg = effective_config(cfg, db, streamer)
    if source.startswith(("http://", "https://")):
        log.info("Lade VOD herunter: %s", source)
        path = download_vod(cfg, source)
        chat_path = Path(chat_file) if chat_file else path.with_suffix(".chat.jsonl")
        if chat_path.exists():
            chat = load_chat_file(chat_path)
        else:
            log.info("Lade Chat-Replay …")
            chat = download_chat(source, chat_path)
    else:
        path = file_url_to_path(source)
        if not path.exists():
            raise FileNotFoundError(path)
        chat = load_chat_file(Path(chat_file) if chat_file else path.with_suffix(".chat.jsonl"))
    info = probe(cfg, path)
    log.info("Analysiere %s (%.0f min, %d Chat-Nachrichten) …", path.name, info["duration"] / 60, len(chat))

    tl = Timeline(keyword_spec(scfg), scfg.get("chat_filter"))
    t0 = time.time()
    MediaAnalyzer(cfg, tl).analyze(path, 0.0, has_audio=info["has_audio"])
    log.info("Bild/Ton analysiert in %.0fs", time.time() - t0)
    for m in chat:
        tl.add_chat(m.ts, m.author, m.text, m.color)

    events: list[dict] = []
    lag = lag if lag is not None else scfg["detection"]["chat_delay"]["vod"]
    det = Detector(tl, scfg, lag, on_event=events.append)
    det.advance(info["duration"])
    det.finalize()
    kept = [e for e in events if not e.get("filtered")]
    log.info("%d Momente gefunden, %d aussortiert", len(kept), len(events) - len(kept))

    ids = []
    slug = slugify(streamer["name"])
    for i, ev in enumerate(events):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        work = data_path(cfg, "clips", slug, f"vod-{stamp}-{i:02d}-{int(ev['score'])}")
        work.mkdir(parents=True, exist_ok=True)
        breakdown = {**ev["breakdown"], "peak_raw": ev["peak"], "event_start_raw": ev["start"],
                     "vod_time": round(ev["peak"])}
        save_clip_chat(work / "chat.json", tl, ev["clip_start"], ev["clip_end"], lag, 0.0)
        filtered = ev.get("filtered")
        cid = db.add_clip(
            streamer_id=streamer["id"], status="filtered" if filtered else "queued",
            source="special" if ev["source"] == "special" else "vod", stream_title=path.stem,
            category=ev["category"], score=ev["score"], breakdown=breakdown, chat_sample=ev["chat_sample"],
            dir=str(work), raw_path=str(path), raw_owned=0, raw_start=ev["clip_start"],
            raw_end=min(ev["clip_end"], info["duration"]), filter_reason=filtered,
        )
        log.info("  #%d  %s  Score %.0f  %s%s", cid, _fmt(ev["peak"]), ev["score"], ev["category"],
                 f"  (aussortiert: {filtered})" if filtered else "")
        if not filtered:
            ids.append(cid)
            processor.enqueue(cid)
    return ids


def _fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
