"""Verarbeitung eines Clip-Kandidaten: Transkription -> Schnitt -> Titel -> Layout -> Untertitel -> Rendern."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
from pathlib import Path

from .chatlog import load_clip_chat
from .config import effective_config
from .db import DB
from .layout import plan_layout
from .render import extract_wav, make_thumbnail, render_clip
from .subtitles import build_ass
from .texts import credit_line, make_texts
from .transcribe import Word, smart_trim, transcribe_locked
from .util import probe

log = logging.getLogger("clipper.processing")

# Formulierungen, mit denen Streamer den Chat direkt ansprechen/abstimmen lassen
QUESTION_PHRASES = ("chat", "was meint ihr", "was denkt ihr", "soll ich", "sollen wir", "wer von euch", "schreibt",
                    "ja oder nein", "votet", "abstimm", "welche", "welcher", "welches", "wie viele", "kennt ihr",
                    "what do you think", "should i", "should we", "who of you", "type in", "yes or no", "which one")


def asked_chat_question(words: list[Word], t0: float, t1: float) -> bool:
    """Hat der Streamer zwischen t0 und t1 dem Chat eine Frage gestellt?"""
    text = " ".join(w.text for w in words if t0 <= w.start <= t1).lower()
    if not text:
        return False
    addressed = any(p in text for p in QUESTION_PHRASES)
    return addressed and ("?" in text or " oder " in text or " or " in text)


def long_window(cfg: dict, start: float, end: float, total: float) -> tuple[float, float] | None:
    """Zeitfenster der TikTok-Langversion (über 61 s): vor allem mehr Vorlauf (Kontext), etwas mehr Reaktion."""
    r = cfg["render"]
    if not r.get("tiktok_long", True) or end - start >= 61 or not total:
        return None
    target = min(max(float(r.get("long_seconds", 70)), 62.0), 90.0)
    extra = target - (end - start)
    ls, le = max(0.0, start - extra * 0.75), min(total, end + extra * 0.25)
    if le - ls < target:
        ls = max(0.0, le - target)
    if le - ls < target:
        le = min(total, ls + target)
    return (round(ls, 3), round(le, 3)) if le - ls >= 62 else None


def render_options(cfg: dict, clip: dict) -> dict:
    """Layout-Optionen: Standard (Einstellungen/Streamer) + Auswahl am Clip."""
    r = cfg["render"]
    opts = {"layout": r.get("layout", "auto"), "cam_size": r.get("cam_size", "m"),
            "chat": bool(r.get("chat_overlay", False))}
    if clip.get("layout_override") and not clip.get("render_opts"):
        opts["layout"] = clip["layout_override"]
    for key, value in (clip.get("render_opts") or {}).items():
        if value is not None and key in opts:
            opts[key] = value
    return opts


class Processor:
    def __init__(self, cfg: dict, db: DB):
        self.cfg = cfg
        self.db = db
        self.q: queue.Queue = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.current: dict[int, str] = {}
        self.on_ready = None   # Callback(clip_id, first): Benachrichtigung/Autopilot

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
                if self.on_ready:
                    clip = self.db.get_clip(clip_id)
                    if clip and clip["status"] == "ready":
                        self.on_ready(clip_id, not rerender)
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
        if clip["status"] == "filtered" and not clip.get("forced"):
            return
        streamer = self.db.get_streamer(clip["streamer_id"]) or {"name": "?", "url": "", "platform": "local"}
        cfg = effective_config(self.cfg, self.db, streamer)
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
            lw = long_window(cfg, start, end, info["duration"])
            a0 = max(0.0, min(start, lw[0] if lw else start) - pad)
            a1 = min(info["duration"] or end + pad, max(end, lw[1] if lw else end) + pad)
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
            lw = long_window(cfg, start, end, info["duration"])
            if lw:
                lw = smart_trim(ws, lw[0], lw[1], 62, 90)
            self.db.update_clip(clip_id, words=words, raw_start=start, raw_end=end, transcript=transcript,
                                long_start=lw[0] if lw else None, long_end=lw[1] if lw else None)
            clip = self.db.get_clip(clip_id)
            bd = clip.get("breakdown") or {}
            q = bd.get("quality") or {}
            if (cfg["filters"].get("enabled", True) and cfg["filters"].get("question_check", True)
                    and clip["source"] in ("auto", "vod") and not clip.get("forced")
                    and isinstance(bd.get("event_start_raw"), (int, float))
                    and q.get("hype_ratio", 1.0) < 0.35 and q.get("stream_reaction", 9.0) < 2.0
                    and asked_chat_question(ws, bd["event_start_raw"] - 15, bd["event_start_raw"] + 2)):
                reason = "Streamer hat den Chat etwas gefragt"
                log.info("Clip %s aussortiert: %s", clip_id, reason)
                self.db.update_clip(clip_id, status="filtered", filter_reason=reason)
                return

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

        # 3) Layout, Untertitel, Rendern
        dur = end - start
        opts = render_options(cfg, clip)
        plan = self._render_version(cfg, clip, streamer, src, info, work, start, end, opts, streamer.get("facecam"),
                                    "final")
        out = work / "final.mp4"
        peak = (clip.get("breakdown") or {}).get("peak_raw")
        thumb_t = min(dur - 0.5, max(0.5, peak - start)) if isinstance(peak, (int, float)) else dur * 0.3
        thumb = work / "thumb.jpg"
        make_thumbnail(cfg, str(out), thumb_t, str(thumb))
        self.db.update_clip(clip_id, status="ready", final_path=str(out), thumb_path=str(thumb), duration=round(dur, 2),
                            layout={**{k: v for k, v in plan.items() if k != "x_keys"}, "opts": opts}, error=None)
        log.info("Clip %s fertig: %s (%.0fs, %s)", clip_id, clip.get("title"), dur, plan["type"])

        # 4) TikTok-Langversion (TikTok vergütet nur Videos über 1 Minute)
        if clip.get("long_start") is not None and cfg["render"].get("tiktok_long", True):
            ls, le = float(clip["long_start"]), min(float(clip["long_end"]), info["duration"] or float(clip["long_end"]))
            if le - ls >= 61:
                try:
                    # Facecam aus der normalen Fassung übernehmen (spart die Erkennung, gleiches Bild)
                    fc = "none"
                    if plan.get("cam"):
                        cx, cy, cw, ch = plan["cam"]
                        fc = {"x": cx / info["width"], "y": cy / info["height"],
                              "w": cw / info["width"], "h": ch / info["height"]}
                    self._render_version(cfg, clip, streamer, src, info, work, ls, le, opts, fc, "final_long")
                    self.db.update_clip(clip_id, long_path=str(work / "final_long.mp4"),
                                        long_duration=round(le - ls, 2))
                    log.info("Clip %s: TikTok-Version fertig (%.0fs)", clip_id, le - ls)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Clip %s: TikTok-Version fehlgeschlagen: %s", clip_id, exc)

    def _render_version(self, cfg: dict, clip: dict, streamer: dict, src: str, info: dict, work: Path, start: float,
                        end: float, opts: dict, facecam, name: str) -> dict:
        dur = end - start
        label = "TikTok-Version" if name != "final" else "Layout"
        self._step(clip["id"], label)
        plan = plan_layout(cfg, src, start, dur, info, facecam, opts)
        clip_words = [Word(s - start, e - start, t) for s, e, t in (clip.get("words") or []) if e > start and s < end]
        chat = None
        if plan.get("chat"):
            chat = [{**m, "t": m["t"] - start} for m in load_clip_chat(work / "chat.json") if m["t"] <= end]
        ass = build_ass(clip_words, plan, cfg, dur, hook=clip.get("hook"), credit=credit_line(streamer), chat=chat)
        ass_path = work / f"{name}.ass"
        ass_path.write_text(ass, encoding="utf-8")
        self._step(clip["id"], f"Rendern{' TikTok-Version' if name != 'final' else ''} ({plan['type']})")
        out, tmp = work / f"{name}.mp4", work / f"{name}.tmp.mp4"
        render_clip(cfg, src, start, dur, plan, info, str(ass_path), str(tmp))
        os.replace(tmp, out)
        return plan


def discard_clip(db: DB, clip_id: int) -> None:
    clip = db.get_clip(clip_id)
    if not clip:
        return
    work = Path(clip["dir"]) if clip.get("dir") else None
    if work and work.exists():
        if clip.get("raw_owned"):
            shutil.rmtree(work, ignore_errors=True)
        else:
            for name in ("final.mp4", "thumb.jpg", "subs.ass", "final.ass", "final.tmp.mp4", "final_long.mp4",
                         "final_long.ass", "final_long.tmp.mp4"):
                (work / name).unlink(missing_ok=True)
    db.update_clip(clip_id, status="discarded")
