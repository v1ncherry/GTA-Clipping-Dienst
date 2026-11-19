"""Signale pro Sekunde: Chat (Rate, Chatter, Emotes, Clip-Rufe), Audio-Lautstärke, Bildbewegung."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path

import numpy as np

from .util import NO_WINDOW, ffmpeg_bin, log

AUDIO_RATE = 8000
AUDIO_STEP = 0.25          # Sekunden pro Lautstärke-Messung
MOTION_FPS = 4
MOTION_W, MOTION_H = 64, 36

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_LAUGH_RE = re.compile(r"\b(?:ha){2,}h?\b|\bxd+\b|\blo+l\b|\bhehe+\b|\bjaja+\b", re.IGNORECASE)

# Typische Antworten, wenn der Streamer den Chat etwas fragt ("ja", "nein", "2", "links" …)
ANSWER_WORDS = {
    "ja", "jaa", "jaaa", "jo", "joa", "jap", "jep", "jup", "nein", "ne", "nee", "nö", "noe", "nope", "yes", "yeah",
    "yep", "yup", "no", "nah", "links", "rechts", "left", "right", "beide", "both", "keins", "keiner", "none",
    "egal", "safe", "sicher", "stimmt", "true", "false", "vielleicht", "maybe", "option", "a", "b", "c", "d",
}
# Chat merkt an, dass der Streamer schläft oder weg ist ("wake up", "is he asleep", "schläft er?", "afk" …)
SLEEP_RE = re.compile(
    r"\bwake\s*(?:up|him|her)\b|\bwakey\b|\bfell\s+asleep\b|\bsleep\s*stream\b|\bafk\b|\bz{3,}\b|residentsleeper"
    r"|\b(?:is|are|he'?s|she'?s|hes|shes|he|she)\s+(?:he\s+|she\s+|bro\s+|u\s+|you\s+)?(?:still\s+)?"
    r"(?:asleep|sleeping|awake)\b"
    r"|\blet\s+(?:him|her|the\s+\w+|bro)\s+sleep\b"
    r"|\bschl(?:ä|ae)ft\b|\beingeschlafen\b|\bpennt\b|\bwach\s+(?:endlich\s+)?auf\b|\baufwachen\b"
    r"|\bist\s+(?:er|sie)\s+(?:noch\s+)?(?:wach|weg)\b",
    re.IGNORECASE)


def is_sleep_message(text: str) -> bool:
    return bool(SLEEP_RE.search(text))


DEFAULT_BOTS = {
    "nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot", "botrixoficial", "kickbot",
    "sery_bot", "soundalerts", "pokemoncommunitygame", "streamstickers", "commanderroot", "botisimo",
}


class KeywordMatcher:
    """Ordnet Chat-Nachrichten Kategorien zu (funny/hype/shock/fail/clip)."""

    def __init__(self, keywords: dict):
        self.cats: dict[str, dict] = {}
        for cat, spec in keywords.items():
            words, phrases, exact = set(), [], set()
            for tok in spec.get("tokens", []):
                t = str(tok).lower().strip()
                if not t:
                    continue
                if len(t) <= 2 and t.isalpha():
                    exact.add(t)            # "W", "L", "F": nur wenn die ganze Nachricht daraus besteht
                elif " " in t or not t.replace("'", "").isalnum():
                    phrases.append(t)       # Mehrwort-Phrasen, Emojis, "?!", "!clip"
                else:
                    words.add(t)
            self.cats[cat] = {"weight": float(spec.get("weight", 1.0)), "words": words, "phrases": phrases,
                              "exact": exact}

    def match(self, text: str) -> list[str]:
        low = text.lower()
        toks = _WORD_RE.findall(low)
        tokset = set(toks)
        hits = []
        for cat, spec in self.cats.items():
            if (spec["words"] & tokset) or any(p in low for p in spec["phrases"]) or (
                    toks and spec["exact"] and all(t in spec["exact"] for t in toks)):
                hits.append(cat)
        if "funny" in self.cats and "funny" not in hits and _LAUGH_RE.search(low):
            hits.append("funny")
        if "shock" in self.cats and "shock" not in hits and "???" in low:
            hits.append("shock")
        return hits

    def weight(self, cat: str) -> float:
        return self.cats.get(cat, {}).get("weight", 1.0)


def is_answer(text: str, hits: list[str]) -> bool:
    """Sieht die Nachricht wie eine Antwort auf eine Frage aus (kurz, ohne Emotes/Lachen)?"""
    if hits:
        return False
    toks = _WORD_RE.findall(text.lower())
    if not toks or len(toks) > 3:
        return False
    if any(t in ANSWER_WORDS or t.isdigit() for t in toks):
        return True
    return len(toks) == 1 and len(toks[0]) <= 14       # einzelnes Wort/Name als Antwort


class Timeline:
    """Zeitreihen in 1-Sekunden-Bins auf der Inhalts-Zeitachse (Sekunden seit Session-/Videostart)."""

    def __init__(self, keywords: dict, chat_filter: dict | None = None):
        self.matcher = KeywordMatcher(keywords)
        self.categories = [c for c in keywords if c != "clip"]
        cf = chat_filter or {}
        self.ignore_commands = bool(cf.get("ignore_commands", True))
        self.bots = {b.lower() for b in cf.get("bots", DEFAULT_BOTS)}
        self.cap = 4096
        self.chat = {k: np.zeros(self.cap)
                     for k in ("msgs", "users", "emotesum", "hypemsgs", "answers", "sleep", "clip", *self.categories)}
        self.noise: list[tuple[float, str]] = []      # Raids, Sub-Geschenkwellen …
        self.audio = np.full(self.cap, np.nan)
        self.motion_sum = np.zeros(self.cap)
        self.motion_n = np.zeros(self.cap)
        self._authors: dict[int, set] = {}
        self.messages: deque = deque(maxlen=60000)
        self.chat_until = 0.0
        self.av_until = 0.0
        self.lock = threading.RLock()

    def update_keywords(self, keywords: dict) -> None:
        """Neue Stichwörter/Kategorien übernehmen (z.B. aus den Einstellungen), ohne Verlauf zu verlieren."""
        with self.lock:
            self.matcher = KeywordMatcher(keywords)
            self.categories = [c for c in keywords if c != "clip"]
            for cat in self.categories:
                self.chat.setdefault(cat, np.zeros(self.cap))

    def _ensure(self, idx: int) -> None:
        if idx < self.cap:
            return
        new_cap = max(self.cap * 2, idx + 1024)
        for k, arr in self.chat.items():
            self.chat[k] = np.concatenate([arr, np.zeros(new_cap - self.cap)])
        self.audio = np.concatenate([self.audio, np.full(new_cap - self.cap, np.nan)])
        self.motion_sum = np.concatenate([self.motion_sum, np.zeros(new_cap - self.cap)])
        self.motion_n = np.concatenate([self.motion_n, np.zeros(new_cap - self.cap)])
        self.cap = new_cap

    # ------------------------------------------------------------------ input
    def add_chat(self, t: float, author: str, text: str, color: str | None = None) -> None:
        if t < 0:
            return
        if author.lower() in self.bots:
            return
        hits = self.matcher.match(text)
        if self.ignore_commands and text.lstrip().startswith("!"):
            # Bot-Befehle (!giveaway, !vote, !discord …) zählen nicht als Hype – nur "!clip" als Clip-Ruf
            if "clip" in hits:
                with self.lock:
                    self._ensure(int(t))
                    self.chat["clip"][int(t)] += 1
            return
        idx = int(t)
        with self.lock:
            self._ensure(idx)
            self.chat["msgs"][idx] += 1
            authors = self._authors.setdefault(idx, set())
            if author not in authors:
                authors.add(author)
                self.chat["users"][idx] += 1
            if len(self._authors) > 64:
                for key in [k for k in self._authors if k < idx - 30]:
                    del self._authors[key]
            hype = 0.0
            for cat in hits:
                if cat == "clip":
                    self.chat["clip"][idx] += 1
                else:
                    self.chat[cat][idx] += 1
                    hype = max(hype, self.matcher.weight(cat))
            self.chat["emotesum"][idx] += hype
            if hype > 0:
                self.chat["hypemsgs"][idx] += 1
            elif is_answer(text, hits):
                self.chat["answers"][idx] += 1
            if is_sleep_message(text):
                self.chat["sleep"][idx] += 1
            self.messages.append((t, author, text, color))
            self.chat_until = max(self.chat_until, t)

    def add_noise(self, t: float, kind: str) -> None:
        """Ereignis, das Chat-Ausbrüche ohne Inhalt auslöst (Raid, Sub-Geschenke)."""
        with self.lock:
            self.noise.append((t, kind))

    def noise_between(self, a: float, b: float) -> list[tuple[float, str]]:
        with self.lock:
            return [n for n in self.noise if a <= n[0] <= b]

    def add_audio(self, t: float, db: float) -> None:
        if t < 0:
            return
        idx = int(t)
        with self.lock:
            self._ensure(idx)
            cur = self.audio[idx]
            self.audio[idx] = db if np.isnan(cur) else max(cur, db)

    def add_motion(self, t: float, value: float) -> None:
        if t < 0:
            return
        idx = int(t)
        with self.lock:
            self._ensure(idx)
            self.motion_sum[idx] += value
            self.motion_n[idx] += 1

    # ------------------------------------------------------------------ output
    def window(self, name: str, a: int, b: int) -> np.ndarray:
        """Werte im Bereich [a, b) – außerhalb der Daten 0 bzw. NaN."""
        n = max(0, b - a)
        if name == "audio":
            out = np.full(n, np.nan)
            src = self.audio
        elif name == "motion":
            out = np.full(n, np.nan)
            src = None
        else:
            out = np.zeros(n)
            src = self.chat[name]
        lo, hi = max(a, 0), min(b, self.cap)
        if hi > lo:
            if name == "motion":
                cnt = self.motion_n[lo:hi]
                with np.errstate(invalid="ignore", divide="ignore"):
                    out[lo - a:hi - a] = np.where(cnt > 0, self.motion_sum[lo:hi] / np.maximum(cnt, 1), np.nan)
            else:
                out[lo - a:hi - a] = src[lo:hi]
        return out

    def messages_between(self, a: float, b: float) -> list[tuple[float, str, str]]:
        with self.lock:
            return [m for m in self.messages if a <= m[0] <= b]


# --------------------------------------------------------------------------- Medienanalyse

class MediaAnalyzer:
    """Liest Audio-Lautstärke + Bewegung aus einer Datei (Segment oder ganzes VOD) in die Timeline."""

    def __init__(self, cfg: dict, timeline: Timeline):
        self.cfg = cfg
        self.tl = timeline
        self.prev_frame: np.ndarray | None = None
        self.has_audio: bool | None = None

    def analyze(self, path: str | Path, offset: float, has_audio: bool = True) -> float:
        """Analysiert die Datei, Zeiten werden um `offset` verschoben. Gibt die analysierte Dauer zurück."""
        fd, motion_path = tempfile.mkstemp(suffix=".motion")
        os.close(fd)
        cmd = [ffmpeg_bin(self.cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path)]
        if has_audio:
            cmd += ["-map", "0:a:0", "-ac", "1", "-ar", str(AUDIO_RATE), "-f", "s16le", "pipe:1"]
        cmd += ["-map", "0:v:0", "-vf", f"fps={MOTION_FPS},scale={MOTION_W}:{MOTION_H},format=gray",
                "-f", "rawvideo", "-y", motion_path]
        audio_secs = 0.0
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if has_audio else subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            if has_audio and proc.stdout:
                chunk = int(AUDIO_RATE * AUDIO_STEP) * 2
                i = 0
                buf = b""
                while True:
                    data = proc.stdout.read(chunk * 16)
                    if not data:
                        break
                    buf += data
                    while len(buf) >= chunk:
                        samples = np.frombuffer(buf[:chunk], dtype=np.int16).astype(np.float32)
                        buf = buf[chunk:]
                        rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0
                        db = 20.0 * np.log10(rms + 1e-6)
                        self.tl.add_audio(offset + i * AUDIO_STEP, db)
                        i += 1
                audio_secs = i * AUDIO_STEP
            _out, err = proc.communicate()
            if proc.returncode != 0:
                log.debug("Analyse-ffmpeg: %s", err.decode("utf-8", "replace")[-500:])
            frame_size = MOTION_W * MOTION_H
            data = Path(motion_path).read_bytes()
            n = len(data) // frame_size
            if n:
                frames = np.frombuffer(data[:n * frame_size], dtype=np.uint8).reshape(n, MOTION_H, MOTION_W)
                frames = frames.astype(np.int16)
                prev = self.prev_frame
                for k in range(n):
                    if prev is not None:
                        val = float(np.mean(np.abs(frames[k] - prev))) / 255.0
                        self.tl.add_motion(offset + k / MOTION_FPS, min(val, 0.5))
                    prev = frames[k]
                self.prev_frame = prev
            video_secs = n / MOTION_FPS
        finally:
            try:
                os.unlink(motion_path)
            except OSError:
                pass
        dur = max(audio_secs, video_secs)
        with self.tl.lock:
            self.tl.av_until = max(self.tl.av_until, offset + dur)
        return dur
