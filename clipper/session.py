"""Live-Session eines Streamers: Aufnahme (Ringpuffer), Chat, Signal-Analyse, Erkennung, Clip-Extraktion."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .chatlog import save_clip_chat
from .config import data_path, effective_config
from .db import DB
from .detector import Detector
from .platforms import LiveInfo, Platform
from .signals import MediaAnalyzer, Timeline
from .util import NO_WINDOW, ffmpeg_bin, popen_guarded, probe, run, slugify

log = logging.getLogger("clipper.session")


def matches_keywords(info: LiveInfo, keywords: list[str]) -> bool:
    if not keywords or info.extra.get("any_category"):
        return True
    text = f"{info.category or ''} {info.title or ''}".lower()
    if not text.strip():
        return True
    return any(k.lower() in text for k in keywords)


def concat_line(path: Path) -> str:
    """Zeile für eine ffmpeg-concat-Liste (einfache Anführungszeichen korrekt escapen)."""
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def cleanup_buffer(cfg: dict, idle_seconds: float = 120) -> tuple[int, int]:
    """Puffer-Ordner nicht sauber beendeter Sessions löschen (nach Absturz). Gedacht für den Start, wenn noch keine
    Session läuft. Ordner, in die gerade noch geschrieben wird, bleiben stehen. Rückgabe: (gelöscht, Bytes frei)."""
    root = data_path(cfg, "buffer")
    if not root.is_dir():
        return 0, 0
    now, removed, freed, busy = time.time(), 0, 0, []
    for streamer_dir in [p for p in root.iterdir() if p.is_dir()]:
        for sess in [p for p in streamer_dir.iterdir() if p.is_dir()]:
            newest, size = sess.stat().st_mtime, 0
            for f in sess.rglob("*"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                newest = max(newest, st.st_mtime)
                size += st.st_size if f.is_file() else 0
            if now - newest < idle_seconds:
                busy.append(sess)
                continue
            shutil.rmtree(sess, ignore_errors=True)
            if not sess.exists():
                removed += 1
                freed += size
        try:
            streamer_dir.rmdir()  # nur wenn leer
        except OSError:
            pass
    if removed:
        log.info("Alte Puffer aufgeräumt: %d Ordner, %.0f MB frei", removed, freed / 1e6)
    for sess in busy:
        log.warning("Puffer %s wird noch beschrieben – läuft eine alte Aufnahme (ffmpeg/streamlink) weiter? "
                    "Ggf. im Task-Manager beenden.", sess)
    return removed, freed


@dataclass
class Segment:
    path: Path
    start: float
    end: float


class Recorder:
    """streamlink | ffmpeg -> 4-Sekunden-TS-Segmente (Stream wird nur kopiert, nicht neu kodiert)."""

    def __init__(self, cfg: dict, platform: Platform, info: LiveInfo, out_dir: Path):
        self.cfg = cfg
        self.platform = platform
        self.info = info
        self.dir = out_dir
        self.p1: subprocess.Popen | None = None
        self.p2: subprocess.Popen | None = None

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        logf = open(self.dir / "capture.log", "ab")  # noqa: SIM115
        seg = str(self.cfg["buffer"]["segment_seconds"])
        src_cmd = self.platform.capture_source_cmd(self.info)
        ff = [ffmpeg_bin(self.cfg), "-hide_banner", "-loglevel", "error", "-fflags", "+genpts+discardcorrupt",
              *self.platform.ffmpeg_input_args(self.info),
              "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "segment", "-segment_time", seg,
              "-segment_list", "list.csv", "-segment_list_type", "csv", "-reset_timestamps", "1", "seg_%06d.ts"]
        # popen_guarded: stirbt der Clipper (Absturz/Task-Manager), beendet Windows auch streamlink und ffmpeg –
        # sonst würden sie endlos Segmente schreiben, die niemand mehr löscht.
        if src_cmd:
            self.p1 = popen_guarded(src_cmd, stdout=subprocess.PIPE, stderr=logf, creationflags=NO_WINDOW)
            self.p2 = popen_guarded(ff, stdin=self.p1.stdout, stderr=logf, cwd=self.dir, creationflags=NO_WINDOW)
            self.p1.stdout.close()  # type: ignore[union-attr]
        else:
            self.p2 = popen_guarded(ff, stdin=subprocess.DEVNULL, stderr=logf, cwd=self.dir, creationflags=NO_WINDOW)
        logf.close()

    def alive(self) -> bool:
        return self.p2 is not None and self.p2.poll() is None

    def stop(self) -> None:
        for p in (self.p1, self.p2):
            if p and p.poll() is None:
                p.terminate()
        for p in (self.p1, self.p2):
            if p:
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()


class LiveSession:
    def __init__(self, streamer: dict, platform: Platform, info: LiveInfo, cfg: dict, db: DB, processor,
                 on_end=None):
        self.streamer = streamer
        self.platform = platform
        self.info = info
        self.base_cfg = cfg
        self.cfg = effective_config(cfg, db, streamer)
        self.db = db
        self.processor = processor
        self.on_end = on_end
        self.slug = slugify(streamer["name"])
        self.started = datetime.now()
        self.dir = data_path(cfg, "buffer", self.slug, self.started.strftime("%Y%m%d-%H%M%S"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.wall0 = time.time()
        self.timeline = Timeline(self.cfg["keywords"], self.cfg.get("chat_filter"))
        self.analyzer = MediaAnalyzer(cfg, self.timeline)
        delays = self.cfg["detection"]["chat_delay"]
        self.detector = Detector(self.timeline, self.cfg, delays.get(platform.name, 5), on_event=self._on_event)
        self.segments: deque[Segment] = deque()
        self.seg_lock = threading.Lock()
        self.pending: list[dict] = []
        self.pending_lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.recorder: Recorder | None = None
        self.part = -1
        self._csv_pos = 0
        self._csv0: float | None = None
        self._anchor = 0.0
        self.has_audio: bool | None = None
        self.paused = not matches_keywords(info, self.cfg["monitor"].get("only_keywords") or [])
        self.chat_connected = False
        self.chat_times: deque = deque(maxlen=20000)
        self.clips_created = 0
        self.snapshot = self.dir / "snapshot.jpg"
        self._last_snapshot = 0.0
        self.ended = False
        self.threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        log.info("Session gestartet: %s (%s) – %s", self.streamer["name"], self.platform.name, self.info.title)
        for target, name in ((self._supervise, "rec"), (self._segments_loop, "seg"), (self._chat_loop, "chat"),
                             (self._detect_loop, "detect")):
            t = threading.Thread(target=target, name=f"{self.slug}-{name}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        if self.stop_evt.is_set():
            return
        self.stop_evt.set()
        if self.recorder:
            self.recorder.stop()

    def content_now(self) -> float:
        return time.time() - self.wall0

    def _start_recorder(self) -> None:
        self.part += 1
        self._csv_pos, self._csv0 = 0, None
        self.analyzer.prev_frame = None
        self.recorder = Recorder(self.base_cfg, self.platform, self.info, self.dir / f"part{self.part}")
        self.recorder.start()

    def _supervise(self) -> None:
        try:
            self._start_recorder()
            last_meta = time.time()
            failures = 0
            poll = self.cfg["monitor"]["poll_seconds"]
            while not self.stop_evt.wait(2):
                if self.recorder and not self.recorder.alive():
                    try:
                        info = self.platform.check_live()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("%s: Live-Check fehlgeschlagen: %s", self.streamer["name"], exc)
                        info = LiveInfo(False)
                    if info.live:
                        failures = 0
                        self.info = info
                        log.info("%s: Aufnahme neu gestartet (Verbindung unterbrochen)", self.streamer["name"])
                        self._start_recorder()
                        continue
                    failures += 1
                    if failures >= 3:
                        log.info("%s: Stream beendet", self.streamer["name"])
                        break
                    self.stop_evt.wait(15)
                    continue
                if time.time() - last_meta > poll:
                    last_meta = time.time()
                    try:
                        info = self.platform.check_live()
                        if info.live:
                            if info.extra.get("chatroom_id") is None:
                                info.extra.update({k: v for k, v in self.info.extra.items() if k not in info.extra})
                            self.info = info
                            paused = not matches_keywords(info, self.cfg["monitor"].get("only_keywords") or [])
                            if paused != self.paused:
                                log.info("%s: %s (%s)", self.streamer["name"],
                                         "pausiert – andere Kategorie" if paused else "passende Kategorie – clippe wieder",
                                         info.category or info.title)
                            self.paused = paused
                    except Exception as exc:  # noqa: BLE001
                        log.debug("Metadaten-Update fehlgeschlagen: %s", exc)
        finally:
            self.stop()
            self._finish()

    def _finish(self) -> None:
        for t in self.threads:
            if t is not threading.current_thread():
                t.join(timeout=15)
        self.detector.finalize()
        self._process_pending(final=True)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.ended = True
        log.info("Session beendet: %s (%d Clips)", self.streamer["name"], self.clips_created)
        if self.on_end:
            self.on_end(self)

    # ------------------------------------------------------------------ segments + signals
    def _segments_loop(self) -> None:
        keep = self.cfg["buffer"]["keep_minutes"] * 60
        while not self.stop_evt.wait(0.5) or self._has_new_lines():
            for fname, s, e in self._read_new_segments():
                part_dir = self.dir / f"part{self.part}"
                path = part_dir / fname
                if not path.exists():
                    continue
                if self._csv0 is None:
                    self._csv0 = s
                    anchor = self.content_now() - (e - s)
                    with self.seg_lock:
                        if self.segments:
                            anchor = max(anchor, self.segments[-1].end)
                    self._anchor = max(0.0, anchor)
                cs, ce = self._anchor + (s - self._csv0), self._anchor + (e - self._csv0)
                if self.has_audio is None:
                    try:
                        self.has_audio = probe(self.base_cfg, path)["has_audio"]
                    except Exception:  # noqa: BLE001
                        self.has_audio = True
                try:
                    self.analyzer.analyze(path, cs, has_audio=bool(self.has_audio))
                except Exception as exc:  # noqa: BLE001
                    log.debug("Segment-Analyse fehlgeschlagen: %s", exc)
                with self.seg_lock:
                    self.segments.append(Segment(path, cs, ce))
                    while self.segments and self.segments[0].end < ce - keep:
                        old = self.segments.popleft()
                        old.path.unlink(missing_ok=True)
                if time.time() - self._last_snapshot > 30:
                    self._last_snapshot = time.time()
                    self._make_snapshot(path)
            if self.stop_evt.is_set():
                break

    def _has_new_lines(self) -> bool:
        csv = self.dir / f"part{self.part}" / "list.csv"
        return csv.exists() and csv.stat().st_size > self._csv_pos

    def _read_new_segments(self) -> list[tuple[str, float, float]]:
        csv = self.dir / f"part{self.part}" / "list.csv"
        if not csv.exists():
            return []
        with open(csv, "rb") as fh:
            fh.seek(self._csv_pos)
            data = fh.read()
        if b"\n" not in data:
            return []
        complete = data[:data.rindex(b"\n") + 1]
        self._csv_pos += len(complete)
        out = []
        for line in complete.decode("utf-8", "replace").splitlines():
            parts = line.strip().split(",")
            if len(parts) >= 3:
                try:
                    out.append((parts[0], float(parts[1]), float(parts[2])))
                except ValueError:
                    pass
        return out

    def _make_snapshot(self, seg: Path) -> None:
        run([ffmpeg_bin(self.base_cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(seg),
             "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", str(self.snapshot)], timeout=30, check=False)

    # ------------------------------------------------------------------ chat
    def _chat_loop(self) -> None:
        try:
            for msg in self.platform.chat_messages(self.info, self.stop_evt):
                t = msg.ts - self.wall0
                if t < -5:
                    continue
                self.chat_connected = True
                if msg.kind != "msg":
                    log.info("%s: %s im Chat – Chat-Ausbrüche der nächsten Sekunden werden nicht geclippt",
                             self.streamer["name"], "Raid" if msg.kind == "raid" else "Sub-Geschenkwelle")
                    self.timeline.add_noise(max(t, 0.0), msg.kind)
                    continue
                self.timeline.add_chat(max(t, 0.0), msg.author, msg.text, msg.color)
                self.chat_times.append(time.time())
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: Chat-Thread beendet: %s", self.streamer["name"], exc)

    # ------------------------------------------------------------------ detection
    def _refresh_config(self) -> None:
        """Änderungen aus dem Dashboard (Regler, Filter, Empfindlichkeit) live übernehmen."""
        streamer = self.db.get_streamer(self.streamer["id"]) or self.streamer
        cfg = effective_config(self.base_cfg, self.db, streamer)
        if cfg != self.cfg:
            self.cfg = cfg
            self.detector.update_config(cfg)
            log.info("%s: neue Einstellungen übernommen", self.streamer["name"])

    def _detect_loop(self) -> None:
        last_lag = time.time()
        last_cfg = time.time()
        while not self.stop_evt.wait(1):
            if time.time() - last_cfg > 10:
                last_cfg = time.time()
                try:
                    self._refresh_config()
                except Exception:  # noqa: BLE001
                    log.exception("Einstellungen konnten nicht neu geladen werden")
            av = self.timeline.av_until
            until = av - 2
            if self.chat_connected:
                until = min(until, self.content_now() - self.detector.lag - 4)
            if until > self.detector.cursor:
                self.detector.advance(until)
            if self.cfg["detection"].get("auto_lag") and time.time() - last_lag > 300:
                last_lag = time.time()
                est = self.detector.estimate_lag(self.detector.cursor)
                if est is not None:
                    self.detector.lag = round(0.7 * self.detector.lag + 0.3 * est, 1)
                    log.info("%s: Chat-Verzögerung geschätzt %.0fs -> nutze %.1fs", self.streamer["name"], est,
                             self.detector.lag)
            self._process_pending()

    def _on_event(self, ev: dict) -> None:
        if self.paused and ev["source"] != "manual":
            log.info("%s: Moment (Score %.0f) ignoriert – andere Kategorie als eingestellt", self.streamer["name"],
                     ev["score"])
            return
        if ev.get("filtered"):
            log.info("%s: Moment bei %.0fs aussortiert: %s", self.streamer["name"], ev["peak"], ev["filtered"])
        else:
            log.info("%s: %s erkannt! Score %.0f (%s) bei %.0fs", self.streamer["name"],
                     "Spezial-Moment" if ev["source"] == "special" else "Hype-Moment", ev["score"],
                     ev["category"], ev["peak"])
        with self.pending_lock:
            self.pending.append(ev)

    def manual_clip(self, seconds: float = 45.0) -> None:
        end = self.timeline.av_until
        if end <= 1:
            raise RuntimeError("Noch keine Aufnahme im Puffer")
        ev = self.detector.manual_event(end, seconds)
        with self.pending_lock:
            self.pending.append(ev)

    def _process_pending(self, final: bool = False) -> None:
        with self.pending_lock:
            ready = [e for e in self.pending if final or e["clip_end"] + 1 <= self.timeline.av_until]
            self.pending = [e for e in self.pending if e not in ready]
        for ev in ready:
            try:
                self._extract(ev)
            except Exception as exc:  # noqa: BLE001
                log.exception("Clip-Extraktion fehlgeschlagen: %s", exc)

    def _extract(self, ev: dict) -> None:
        start, end = ev["clip_start"], ev["clip_end"]
        with self.seg_lock:
            segs = [s for s in self.segments if s.end > start - 0.5 and s.start < end + 0.5 and s.path.exists()]
        if not segs:
            log.warning("%s: keine Segmente für %.0f-%.0fs im Puffer", self.streamer["name"], start, end)
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        work = data_path(self.base_cfg, "clips", self.slug, f"{stamp}-{int(ev['score'])}")
        work.mkdir(parents=True, exist_ok=True)
        listing = work / "segments.txt"
        listing.write_text("".join(concat_line(s.path) for s in segs), encoding="utf-8")
        raw = work / "raw.ts"
        run([ffmpeg_bin(self.base_cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat",
             "-safe", "0", "-i", str(listing), "-map", "0", "-c", "copy", str(raw)], timeout=300)
        listing.unlink(missing_ok=True)
        base = segs[0].start
        breakdown = dict(ev["breakdown"])
        breakdown["peak_raw"] = round(ev["peak"] - base, 2)
        breakdown["event_start_raw"] = round(ev["start"] - base, 2)
        save_clip_chat(work / "chat.json", self.timeline, start, end, self.detector.lag, base)
        filtered = ev.get("filtered")
        cid = self.db.add_clip(
            streamer_id=self.streamer["id"], status="filtered" if filtered else "queued", source=ev["source"],
            stream_title=self.info.title, category=ev["category"], score=ev["score"], breakdown=breakdown,
            chat_sample=ev["chat_sample"], dir=str(work), raw_path=str(raw), raw_owned=1,
            raw_start=round(max(0.0, start - base), 3), raw_end=round(end - base, 3), filter_reason=filtered,
        )
        if not filtered:
            self.clips_created += 1
            self.processor.enqueue(cid)

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        now = time.time()
        recent = sum(1 for t in self.chat_times if t > now - 60)
        with self.seg_lock:
            buffered = (self.segments[-1].end - self.segments[0].start) if self.segments else 0
        return {
            "live": True, "title": self.info.title, "category": self.info.category, "paused": self.paused,
            "started": self.wall0, "recording": bool(self.recorder and self.recorder.alive()),
            "hype": self.detector.history[-1][1] if self.detector.history else 0,
            "parts": self.detector.last_parts, "chat_per_min": recent, "chat_connected": self.chat_connected,
            "lag": self.detector.lag, "clips": self.clips_created, "buffer_seconds": round(buffered),
            "warmup": self.detector.cursor < self.detector.warmup_bins,
        }

    def history(self, n: int = 600) -> list:
        return list(self.detector.history)[-n:]
