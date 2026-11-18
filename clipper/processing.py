"""Verarbeitung eines Clip-Kandidaten: Transkription -> Schnitt -> Titel -> Layout -> Untertitel -> Rendern."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
from pathlib import Path

from .config import streamer_config
from .db import DB
from .layout import plan_layout
from .render import extract_wav, make_thumbnail, render_clip
from .subtitles import build_ass
from .texts import credit_line, make_texts
from .transcribe import Word, smart_trim, transcribe_locked
from .util import probe

log = logging.getLogger("clipper.processing")


class Processor:
    def __init__(self, cfg: dict, db: DB):
        self.cfg = cfg
        self.db = db
        self.q: queue.Queue = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.current: dict[int, str] = {}

    def start(self) -> None:
        for i in range(max(1, int(self.cfg.get("workers", 2)))):
            t = threading.Thread(target=self._worker, name=f"processor-{i}", daemon=True)
            t.start()
            self.threads.append(t)
        for clip in self.db.query("SELECT id FROM clips WHERE status IN ('queued', 'processing') ORDER BY id"):
            self.q.put((clip["id"], False))

    def enqueue(self, clip_id: int, rerender: bool = False) -> None:
        self.db.update_clip(clip_id, status="queued", error=None)
        self.q.put((clip_id, rerender))

    def wait_idle(self) -> None:
        self.q.join()

    def _worker(self) -> None:
        while True:
            clip_id, rerender = self.q.get()
            try:
                self.process(clip_id, rerender)
            except Exception as exc:  # noqa: BLE001
                log.exception("Clip %s fehlgeschlagen", clip_id)
                self.db.update_clip(clip_id, status="failed", error=str(exc)[-1500:])
            finally:
                self.current.pop(clip_id, None)
                self.q.task_done()

    def _step(self, clip_id: int, text: str) -> None:
        self.current[clip_id] = text
        log.info("Clip %s: %s", clip_id, text)

    def process(self, clip_id: int, rerender: bool = False) -> None:
        clip = self.db.get_clip(clip_id)
        if not clip or clip["status"] in ("discarded", "uploaded") and not rerender:
            return
        streamer = self.db.get_streamer(clip["streamer_id"]) or {"name": "?", "url": "", "platform": "local"}
        cfg = streamer_config(self.cfg, streamer)
        self.db.update_clip(clip_id, status="processing", error=None)
        work = Path(clip["dir"])
        work.mkdir(parents=True, exist_ok=True)
        src = clip["raw_path"]
        if not src or not Path(src).exists():
            raise RuntimeError(f"Rohvideo fehlt: {src}")
        info = probe(cfg, src)
        start, end = float(clip["raw_start"]), float(clip["raw_end"])
        if info["duration"]:
            end = min(end, info["duration"])

        # 1) Transkription mit Wort-Zeitstempeln (+ Schnitt an Satzgrenzen)
        words = clip.get("words")
        if words is None:
            self._step(clip_id, "Transkription")
            pad = 4.0
            a0 = max(0.0, start - pad)
            a1 = min(info["duration"] or end + pad, end + pad)
            ws: list[Word] = []
            if info["has_audio"]:
                wav = work / "audio.wav"
                extract_wav(cfg, src, a0, a1 - a0, str(wav))
                ws = [Word(w.start + a0, w.end + a0, w.text)
                      for w in transcribe_locked(cfg, str(wav), cfg["transcription"].get("language"))]
                wav.unlink(missing_ok=True)
            if cfg["clips"].get("smart_trim", True) and clip["source"] != "manual":
                start, end = smart_trim(ws, start, end, cfg["clips"]["min_seconds"], cfg["clips"]["max_seconds"])
            words = [w.to_list() for w in ws]
            transcript = " ".join(w.text for w in ws if start - 0.2 <= w.start <= end)
            self.db.update_clip(clip_id, words=words, raw_start=start, raw_end=end, transcript=transcript)
            clip = self.db.get_clip(clip_id)

        # 2) Titel, Hook, Hashtags
        if not clip.get("title"):
            self._step(clip_id, "Titel & Hashtags")
            texts = make_texts(cfg, streamer, clip)
            breakdown = clip.get("breakdown") or {}
            if texts.get("llm_reason"):
                breakdown["llm_reason"] = texts["llm_reason"]
            self.db.update_clip(clip_id, title=texts["title"], hook=texts["hook"], hashtags=texts["hashtags"],
                                caption=texts["caption"], llm_score=texts.get("llm_score"), breakdown=breakdown)
            clip = self.db.get_clip(clip_id)

        # 3) Layout (Facecam + Smart-Crop)
        dur = end - start
        self._step(clip_id, "Layout")
        plan = plan_layout(cfg, src, start, dur, info, streamer.get("facecam"), clip.get("layout_override"))

        # 4) Untertitel
        clip_words = [Word(s - start, e - start, t) for s, e, t in (clip.get("words") or []) if e > start and s < end]
        ass = build_ass(clip_words, plan, cfg, dur, hook=clip.get("hook"), credit=credit_line(streamer))
        ass_path = work / "subs.ass"
        ass_path.write_text(ass, encoding="utf-8")

        # 5) Rendern
        self._step(clip_id, f"Rendern ({plan['type']})")
        out = work / "final.mp4"
        tmp = work / "final.tmp.mp4"
        render_clip(cfg, src, start, dur, plan, info, str(ass_path), str(tmp))
        os.replace(tmp, out)
        peak = (clip.get("breakdown") or {}).get("peak_raw")
        thumb_t = min(dur - 0.5, max(0.5, peak - start)) if isinstance(peak, (int, float)) else dur * 0.3
        thumb = work / "thumb.jpg"
        make_thumbnail(cfg, str(out), thumb_t, str(thumb))
        self.db.update_clip(clip_id, status="ready", final_path=str(out), thumb_path=str(thumb), duration=round(dur, 2),
                            layout={k: v for k, v in plan.items() if k != "x_keys"}, error=None)
        log.info("Clip %s fertig: %s (%.0fs, %s)", clip_id, clip.get("title"), dur, plan["type"])


def discard_clip(db: DB, clip_id: int) -> None:
    clip = db.get_clip(clip_id)
    if not clip:
        return
    work = Path(clip["dir"]) if clip.get("dir") else None
    if work and work.exists():
        if clip.get("raw_owned"):
            shutil.rmtree(work, ignore_errors=True)
        else:
            for name in ("final.mp4", "thumb.jpg", "subs.ass", "final.tmp.mp4"):
                (work / name).unlink(missing_ok=True)
    db.update_clip(clip_id, status="discarded")
