"""Hype-Erkennung: kombiniert Chat- und Stream-Signale zu einem Score (0-100) und findet clipwürdige Momente.

Alle Signale werden gegen die eigene Baseline des Streamers normalisiert (robuster z-Score über die letzten
Minuten). Dadurch funktioniert die Erkennung bei 500 wie bei 50.000 Zuschauern gleich.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from typing import Callable

import numpy as np

from .signals import Timeline

SERIES = ("chat_rate", "chatters", "emotes", "audio", "motion")


def _floor(name: str, med: float) -> float:
    if name in ("chat_rate", "chatters", "emotes"):
        return max(1.0, 0.5 * math.sqrt(max(med, 0) + 1), 0.15 * med)
    if name == "audio":
        return 2.0
    return max(0.003, 0.2 * med)


def score_from_raw(raw: float) -> float:
    return 100.0 / (1.0 + math.exp(-(raw - 2.5) / 0.7))


class Detector:
    def __init__(self, timeline: Timeline, cfg: dict, lag: float, on_event: Callable[[dict], None] | None = None):
        self.tl = timeline
        self.d = cfg["detection"]
        self.c = cfg["clips"]
        self.lag = float(lag)
        self.on_event = on_event
        self.cursor = 0
        self.cap = 4096
        self.sm = {k: np.full(self.cap, np.nan) for k in SERIES}
        self.scores = np.zeros(self.cap)
        self.baseline_bins = int(self.d["baseline_minutes"] * 60)
        self.warmup_bins = int(self.d["warmup_minutes"] * 60)
        self._stats: dict[str, tuple[int, float, float]] = {}
        self.active: dict | None = None
        self._run = 0
        self._run_start: int | None = None
        self.last_event_end = -1e9
        self.recent_events: deque = deque()
        self.events: list[dict] = []
        self.history: deque = deque(maxlen=1800)
        self.last_parts: dict = {}

    # ------------------------------------------------------------------ helpers
    def _ensure(self, idx: int) -> None:
        if idx < self.cap:
            return
        new_cap = max(self.cap * 2, idx + 1024)
        for k in SERIES:
            self.sm[k] = np.concatenate([self.sm[k], np.full(new_cap - self.cap, np.nan)])
        self.scores = np.concatenate([self.scores, np.zeros(new_cap - self.cap)])
        self.cap = new_cap

    def _raw_values(self, idx: int) -> dict:
        c = idx + int(round(self.lag))
        tl = self.tl
        audio = tl.window("audio", idx - 1, idx + 2)
        motion = tl.window("motion", idx - 1, idx + 2)
        return {
            "chat_rate": float(tl.window("msgs", c - 2, c + 3).sum()),
            "chatters": float(tl.window("users", c - 2, c + 3).sum()),
            "emotes": float(tl.window("emotesum", c - 2, c + 3).sum()),
            "clip": float(tl.window("clip", c - 2, c + 9).sum()),
            "audio": float(np.nanmax(audio)) if np.any(~np.isnan(audio)) else math.nan,
            "motion": float(np.nanmean(motion)) if np.any(~np.isnan(motion)) else math.nan,
        }

    def _z(self, name: str, idx: int, value: float) -> float:
        if math.isnan(value):
            return 0.0
        cached = self._stats.get(name)
        if cached is None or idx - cached[0] >= 5:
            a, b = max(0, idx - self.baseline_bins), max(0, idx - 20)
            base = self.sm[name][a:b]
            base = base[~np.isnan(base)]
            if len(base) < max(30, self.warmup_bins):
                self._stats[name] = (idx, math.nan, math.nan)
            else:
                med = float(np.median(base))
                mad = float(np.median(np.abs(base - med))) * 1.4826
                self._stats[name] = (idx, med, max(mad, _floor(name, med)))
            cached = self._stats[name]
        _, med, scale = cached
        if math.isnan(med):
            return 0.0
        return float(np.clip((value - med) / scale, -3.0, 10.0))

    # ------------------------------------------------------------------ main loop
    def advance(self, until: float) -> None:
        """Wertet alle Sekunden bis `until` (Inhalts-Zeit, exklusiv) aus."""
        until = int(until)
        while self.cursor < until:
            idx = self.cursor
            self._ensure(idx)
            vals = self._raw_values(idx)
            for k in SERIES:
                self.sm[k][idx] = vals[k]
            z = {k: self._z(k, idx, vals[k]) for k in SERIES}
            w = self.d["weights"]
            wsum = sum(w.values()) or 1.0
            full = sum(w.get(k, 0) * z[k] for k in SERIES) / wsum
            full += self.d["clip_request_bonus"] * min(vals["clip"], 5)
            sw = self.d["solo_weights"]
            swsum = sum(sw.values()) or 1.0
            solo = self.d["solo_factor"] * (sw.get("audio", 0) * z["audio"] + sw.get("motion", 0) * z["motion"]) / swsum
            raw = max(full, solo)
            score = score_from_raw(raw)
            self.scores[idx] = score
            parts = {**{k: round(z[k], 2) for k in SERIES}, "clip_requests": int(vals["clip"]),
                     "raw": round(raw, 2), "mode": "stream" if solo > full else "chat"}
            self.last_parts = parts
            self.history.append((idx, round(score, 1)))
            self._update(idx, score, parts)
            self.cursor += 1

    def _update(self, idx: int, score: float, parts: dict) -> None:
        trig, rel = self.d["trigger_score"], self.d["release_score"]
        if self.active is None:
            if score >= trig:
                if self._run_start is None:
                    self._run_start = idx
                self._run += 1
                if self._run >= self.d["min_event_seconds"]:
                    start = self._run_start
                    while start - 1 >= 0 and self.scores[start - 1] >= rel and self._run_start - start < 15:
                        start -= 1
                    peak = int(np.argmax(self.scores[self._run_start:idx + 1])) + self._run_start
                    self.active = {"start": start, "peak": peak, "peak_score": float(self.scores[peak]),
                                   "parts": parts, "below": 0}
            else:
                self._run, self._run_start = 0, None
            return
        a = self.active
        if score > a["peak_score"]:
            a.update(peak=idx, peak_score=score, parts=parts)
        a["below"] = a["below"] + 1 if score < rel else 0
        if a["below"] >= 3 or idx - a["start"] >= self.d["max_event_seconds"]:
            self._finish(a, idx - a["below"])

    def finalize(self) -> None:
        if self.active:
            self._finish(self.active, self.cursor - 1)

    def _finish(self, a: dict, end: int) -> None:
        self.active = None
        self._run, self._run_start = 0, None
        start, peak, peak_score = a["start"], a["peak"], a["peak_score"]
        if start < self.last_event_end + self.d["cooldown_seconds"]:
            return
        while self.recent_events and self.recent_events[0] < peak - 3600:
            self.recent_events.popleft()
        if len(self.recent_events) >= self.d["max_clips_per_hour"] and peak_score < self.d["over_limit_score"]:
            return
        self.last_event_end = end
        self.recent_events.append(peak)
        event = self.build_event(start, end, peak, peak_score, a["parts"], source="auto")
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    # ------------------------------------------------------------------ events
    def clip_window(self, start: float, end: float, peak: float) -> tuple[float, float]:
        pre, post = self.c["pre_roll"], self.c["post_roll"]
        mn, mx = self.c["min_seconds"], self.c["max_seconds"]
        s = start - pre
        e = max(end + post, peak + 6)
        if e - s > mx:
            s = max(s, peak - 0.65 * mx)
            e = min(e, s + mx)
        if e - s < mn:
            deficit = mn - (e - s)
            s -= deficit * 0.6
            e += deficit * 0.4
        if s < 0:
            e, s = e - s, 0.0
        return round(s, 2), round(e, 2)

    def build_event(self, start: float, end: float, peak: float, score: float, parts: dict, source: str) -> dict:
        lag = int(round(self.lag))
        a, b = int(start) + lag - 2, int(end) + lag + 8
        cats = {c: float(self.tl.window(c, a, b).sum()) for c in self.tl.categories}
        if sum(cats.values()) > 0:
            category = max(cats, key=cats.get)
        else:
            category = "action" if parts.get("mode") == "stream" else "hype"
        msgs = self.tl.messages_between(a, b)
        counter = Counter(" ".join(m[2].split())[:80] for m in msgs)
        clip_start, clip_end = self.clip_window(start, end, peak)
        return {
            "source": source,
            "start": float(start), "end": float(end), "peak": float(peak),
            "score": round(float(score), 1),
            "category": category,
            "clip_start": clip_start, "clip_end": clip_end,
            "breakdown": {**parts, "lag": round(self.lag, 1), "categories": cats, "messages": len(msgs)},
            "chat_sample": [[t, n] for t, n in counter.most_common(15)],
        }

    def manual_event(self, end: float, seconds: float) -> dict:
        start = max(0.0, end - seconds)
        ev = self.build_event(start, end, end, self.scores[int(end)] if end < self.cap else 0, self.last_parts,
                              source="manual")
        ev["clip_start"], ev["clip_end"] = round(start, 2), round(end, 2)
        return ev

    # ------------------------------------------------------------------ lag
    def estimate_lag(self, upto: int, span: int = 1800, max_lag: int = 20) -> float | None:
        """Chat-Verzögerung schätzen: bei welchem Versatz korreliert Chat-Aktivität am stärksten mit Lautstärke?"""
        a = max(0, upto - span)
        n = upto - a
        if n < 600:
            return None
        audio = self.tl.window("audio", a, upto)
        if np.all(np.isnan(audio)):
            return None
        audio = np.nan_to_num(audio, nan=float(np.nanmedian(audio)))
        audio = np.clip(audio - np.median(audio), 0, None)
        msgs = self.tl.window("msgs", a, upto + max_lag + 5)
        msgs = np.convolve(msgs, np.ones(5), mode="same")
        if msgs.std() == 0 or audio.std() == 0:
            return None
        best, best_r = None, 0.12
        for lag in range(0, max_lag + 1):
            r = float(np.corrcoef(audio, msgs[lag:lag + n])[0, 1])
            if r > best_r:
                best, best_r = lag, r
        return float(best) if best is not None else None
