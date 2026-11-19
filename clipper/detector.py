"""Hype-Erkennung: kombiniert Chat- und Stream-Signale zu einem Score (0-100) und findet clipwürdige Momente.

Alle Signale werden gegen die eigene Baseline des Streamers normalisiert (robuster z-Score über die letzten
Minuten). Dadurch funktioniert die Erkennung bei 500 wie bei 50.000 Zuschauern gleich.

Drei Spuren:
1. Hype       – Chat + Stream kombiniert (Gewichtung über Regler), danach Qualitätsprüfung gegen leere
                Chat-Ausbrüche (Antworten auf Fragen, Raids, Spam ohne Reaktion im Stream).
2. Spezial    – Chat erwähnt auffällig oft Easter Eggs/Geheimnisse oder Glitches/Bugs.
3. Stille     – sonst aktiver Chat wird plötzlich still (Spannung, Story, alle schauen gebannt zu).
"""

from __future__ import annotations

import math
from collections import Counter, deque
from typing import Callable

import numpy as np

from .signals import Timeline

SERIES = ("chat_rate", "chatters", "emotes", "audio", "motion")
CHAT_KEYS = ("chat_rate", "chatters", "emotes")
STREAM_KEYS = ("audio", "motion")


def _floor(name: str, med: float) -> float:
    if name in ("chat_rate", "chatters", "emotes", "special"):
        return max(1.0, 0.5 * math.sqrt(max(med, 0) + 1), 0.15 * med)
    if name == "audio":
        return 2.0
    return max(0.003, 0.2 * med)


def score_from_raw(raw: float) -> float:
    return 100.0 / (1.0 + math.exp(-(raw - 2.5) / 0.7))


def effective_weights(d: dict) -> tuple[dict[str, float], float]:
    """Hauptregler (stream_share) + Feinregler (weights) -> normierte Gewichte und Faktor der Stream-Solo-Spur."""
    share = min(max(float(d.get("stream_share", 35)), 0.0), 100.0) / 100.0
    w = {k: max(0.0, float(v)) for k, v in (d.get("weights") or {}).items()}
    cw = sum(w.get(k, 0.0) for k in CHAT_KEYS)
    sw = sum(w.get(k, 0.0) for k in STREAM_KEYS)
    eff = {k: (1 - share) * (w.get(k, 0.0) / cw if cw else 1 / len(CHAT_KEYS)) for k in CHAT_KEYS}
    eff.update({k: share * (w.get(k, 0.0) / sw if sw else 1 / len(STREAM_KEYS)) for k in STREAM_KEYS})
    solo = float(d.get("solo_factor", 0.85)) * min(1.25, share / 0.35)
    return eff, solo


class Detector:
    def __init__(self, timeline: Timeline, cfg: dict, lag: float, on_event: Callable[[dict], None] | None = None):
        self.tl = timeline
        self.d = cfg["detection"]
        self.c = cfg["clips"]
        self.f = cfg.get("filters") or {}
        self.sp = cfg.get("special") or {}
        self.weights, self.solo_factor = effective_weights(self.d)
        self.lag = float(lag)
        self.on_event = on_event
        self.cursor = 0
        self.cap = 4096
        self.sm = {k: np.full(self.cap, np.nan) for k in (*SERIES, "special")}
        self.za = np.zeros(self.cap)
        self.zm = np.zeros(self.cap)
        self.scores = np.zeros(self.cap)
        self.baseline_bins = int(self.d["baseline_minutes"] * 60)
        self.warmup_bins = int(self.d["warmup_minutes"] * 60)
        self._stats: dict[str, tuple[int, float, float]] = {}
        self.active: dict | None = None
        self._run = 0
        self._run_start: int | None = None
        self.last_event_end = -1e9
        self.recent_events: deque = deque()
        self._special_last = -1e9
        self._special_recent: deque = deque()
        self._quiet_start: int | None = None
        self._quiet_msgs = 0.0
        self.events: list[dict] = []
        self.history: deque = deque(maxlen=1800)
        self.last_parts: dict = {}

    def update_config(self, cfg: dict) -> None:
        """Neue Einstellungen übernehmen, ohne den gelernten Normalpegel zu verlieren."""
        self.d = cfg["detection"]
        self.c = cfg["clips"]
        self.f = cfg.get("filters") or {}
        self.sp = cfg.get("special") or {}
        self.weights, self.solo_factor = effective_weights(self.d)

    # ------------------------------------------------------------------ helpers
    def _ensure(self, idx: int) -> None:
        if idx < self.cap:
            return
        new_cap = max(self.cap * 2, idx + 1024)
        grow = new_cap - self.cap
        for k in self.sm:
            self.sm[k] = np.concatenate([self.sm[k], np.full(grow, np.nan)])
        self.za = np.concatenate([self.za, np.zeros(grow)])
        self.zm = np.concatenate([self.zm, np.zeros(grow)])
        self.scores = np.concatenate([self.scores, np.zeros(grow)])
        self.cap = new_cap

    def _raw_values(self, idx: int) -> dict:
        c = idx + int(round(self.lag))
        tl = self.tl
        audio = tl.window("audio", idx - 1, idx + 2)
        motion = tl.window("motion", idx - 1, idx + 2)
        special = sum(float(tl.window(cat, c - 7, c + 8).sum())
                      for cat in self.sp.get("categories", []) if cat in tl.chat)
        return {
            "chat_rate": float(tl.window("msgs", c - 2, c + 3).sum()),
            "chatters": float(tl.window("users", c - 2, c + 3).sum()),
            "emotes": float(tl.window("emotesum", c - 2, c + 3).sum()),
            "clip": float(tl.window("clip", c - 2, c + 9).sum()),
            "special": special,
            "audio": float(np.nanmax(audio)) if np.any(~np.isnan(audio)) else math.nan,
            "motion": float(np.nanmean(motion)) if np.any(~np.isnan(motion)) else math.nan,
        }

    def _baseline(self, name: str, idx: int) -> tuple[float, float]:
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
        return cached[1], cached[2]

    def _z(self, name: str, idx: int, value: float) -> float:
        if math.isnan(value):
            return 0.0
        med, scale = self._baseline(name, idx)
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
            for k in (*SERIES, "special"):
                self.sm[k][idx] = vals[k]
            z = {k: self._z(k, idx, vals[k]) for k in SERIES}
            self.za[idx], self.zm[idx] = z["audio"], z["motion"]
            full = sum(self.weights[k] * z[k] for k in SERIES)
            full += self.d["clip_request_bonus"] * min(vals["clip"], 5)
            sw = self.d["solo_weights"]
            swsum = sum(sw.values()) or 1.0
            solo = self.solo_factor * (sw.get("audio", 0) * z["audio"] + sw.get("motion", 0) * z["motion"]) / swsum
            raw = max(full, solo)
            score = score_from_raw(raw)
            self.scores[idx] = score
            parts = {**{k: round(z[k], 2) for k in SERIES}, "clip_requests": int(vals["clip"]),
                     "raw": round(raw, 2), "mode": "stream" if solo > full else "chat"}
            self.last_parts = parts
            self.history.append((idx, round(score, 1)))
            self._update(idx, score, parts)
            if self.sp.get("enabled", True):
                self._special_mentions(idx, vals, parts)
                self._silence(idx, vals, parts)
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
        quality, reason = self.assess(start, end, a["parts"])
        if reason is None:
            while self.recent_events and self.recent_events[0] < peak - 3600:
                self.recent_events.popleft()
            if len(self.recent_events) >= self.d["max_clips_per_hour"] and peak_score < self.d["over_limit_score"]:
                return
            self.recent_events.append(peak)
        self.last_event_end = end
        event = self.build_event(start, end, peak, peak_score, a["parts"], source="auto")
        event["quality"], event["filtered"] = quality, reason
        event["breakdown"]["quality"] = quality
        self._emit(event)

    def _emit(self, event: dict) -> None:
        event.setdefault("filtered", None)
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    # ------------------------------------------------------------------ Qualität / Filter
    def assess(self, start: float, end: float, parts: dict) -> tuple[dict, str | None]:
        """Ist der Chat-Ausbruch echter Hype – oder z.B. nur Antworten auf eine Frage des Streamers?"""
        lag = int(round(self.lag))
        a, b = int(start) + lag - 2, int(end) + lag + 8
        tl = self.tl
        msgs = float(tl.window("msgs", a, b).sum())
        hype_ratio = float(tl.window("hypemsgs", a, b).sum()) / msgs if msgs else 0.0
        answer_ratio = float(tl.window("answers", a, b).sum()) / msgs if msgs else 0.0
        clips = float(tl.window("clip", a, b).sum())
        s0, s1 = max(0, int(start) - 3), min(self.cap, int(end) + 3)
        reaction = float(max(self.za[s0:s1].max(initial=0.0), 0.8 * self.zm[s0:s1].max(initial=0.0)))
        raid_window = float(self.f.get("raid_seconds", 90))
        noise = tl.noise_between(a - raid_window, b)
        quality = {"hype_ratio": round(hype_ratio, 2), "answer_ratio": round(answer_ratio, 2),
                   "stream_reaction": round(reaction, 2), "messages": int(msgs), "clip_requests": int(clips),
                   "raid": bool(noise)}
        reason = None
        if self.f.get("enabled", True):
            min_hype = float(self.f.get("min_hype_ratio", 0.25))
            if noise:
                reason = "Raid/Sub-Welle im Chat"
            elif parts.get("mode") == "chat" and clips < 3:
                if answer_ratio >= float(self.f.get("max_answer_ratio", 0.45)) and hype_ratio < min_hype \
                        and reaction < 2.0:
                    reason = "Chat beantwortet eine Frage"
                elif hype_ratio < min_hype and reaction < float(self.f.get("min_stream_reaction", 1.0)):
                    reason = "Chat-Ausbruch ohne Reaktion im Stream"
        return quality, reason

    # ------------------------------------------------------------------ Spezial-Momente
    def _special_allowed(self, idx: int) -> bool:
        if self.active is not None or idx < self.last_event_end + 10:
            return False
        if idx - self._special_last < float(self.sp.get("cooldown_seconds", 90)):
            return False
        while self._special_recent and self._special_recent[0] < idx - 3600:
            self._special_recent.popleft()
        return len(self._special_recent) < int(self.sp.get("max_per_hour", 4))

    def _special_mentions(self, idx: int, vals: dict, parts: dict) -> None:
        cnt = vals["special"]
        if cnt < float(self.sp.get("min_mentions", 4)) or not self._special_allowed(idx):
            return
        z = self._z("special", idx, cnt)
        if z < float(self.sp.get("min_z", 3.0)):
            return
        lag = int(round(self.lag))
        c = idx + lag
        cats = [cat for cat in self.sp.get("categories", []) if cat in self.tl.chat]
        counts = {cat: float(self.tl.window(cat, c - 7, c + 8).sum()) for cat in cats}
        category = max(counts, key=counts.get) if counts else "easteregg"
        # Moment = erste Erwähnung im Chat minus Chat-Verzögerung
        hits = sum(self.tl.window(cat, c - 7, c + 8) for cat in cats)
        moment = c - 7 + int(np.argmax(hits > 0)) - lag
        score = float(np.clip(55 + 8 * z, 55, 95))
        window = self._fit_window(moment - 25, moment + 12)
        self._special_last = idx
        self._special_recent.append(idx)
        self.last_event_end = max(self.last_event_end, moment + 5)   # kein doppelter Hype-Clip vom selben Moment
        ev = self.build_event(moment - 10, moment + 5, moment, score, parts, source="special", category=category,
                              window=window)
        ev["breakdown"]["special_mentions"] = int(cnt)
        if category == "stichwort":
            texts = [m[2].lower() for m in self.tl.messages_between(c - 7, c + 8)]
            words = [str(w) for w in self.sp.get("keywords") or []]
            if words:
                ev["breakdown"]["keyword"] = max(words, key=lambda w: sum(w.lower() in t for t in texts))
        self._emit(ev)

    def _silence(self, idx: int, vals: dict, parts: dict) -> None:
        if not self.sp.get("silence", True):
            return
        med, _scale = self._baseline("chat_rate", idx)
        if math.isnan(med) or med < float(self.sp.get("silence_min_baseline", 8)) \
                or math.isnan(vals["audio"]) or math.isnan(vals["motion"]):
            self._quiet_start = None          # zu kleiner Chat oder Lücke in der Aufnahme (z.B. Werbepause)
            return
        quiet = vals["chat_rate"] <= float(self.sp.get("silence_ratio", 0.25)) * med
        if quiet:
            if self._quiet_start is None:
                self._quiet_start, self._quiet_msgs = idx, 0.0
            self._quiet_msgs += self.tl.window("msgs", idx + int(round(self.lag)), idx + int(round(self.lag)) + 1).sum()
            return
        if self._quiet_start is None:
            return
        start, length = self._quiet_start, idx - self._quiet_start
        self._quiet_start = None
        # Ganz ohne Nachrichten ist eher der Chat abgerissen als gebannt
        if length < float(self.sp.get("silence_seconds", 20)) or length > 240 or self._quiet_msgs < 2:
            return
        if not self._special_allowed(idx):
            return
        self._special_last = idx
        self._special_recent.append(idx)
        score = float(np.clip(60 + (length - 20) * 0.8, 60, 90))
        window = self._fit_window(start - 5, idx + 10, keep_end=True)
        ev = self.build_event(start, idx, idx, score, parts, source="special", category="spannung", window=window)
        ev["breakdown"]["silence_seconds"] = int(length)
        self._emit(ev)

    def _fit_window(self, s: float, e: float, keep_end: bool = False) -> tuple[float, float]:
        mn, mx = self.c["min_seconds"], self.c["max_seconds"]
        if e - s > mx:
            if keep_end:
                s = e - mx
            else:
                e = s + mx
        if e - s < mn:
            s -= (mn - (e - s))
        return round(max(0.0, s), 2), round(e, 2)

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

    def build_event(self, start: float, end: float, peak: float, score: float, parts: dict, source: str,
                    category: str | None = None, window: tuple[float, float] | None = None) -> dict:
        lag = int(round(self.lag))
        a, b = int(start) + lag - 2, int(end) + lag + 8
        cats = {c: float(self.tl.window(c, a, b).sum()) for c in self.tl.categories}
        if category is None:
            if sum(cats.values()) > 0:
                category = max(cats, key=cats.get)
            else:
                category = "action" if parts.get("mode") == "stream" else "hype"
        msgs = self.tl.messages_between(a, b)
        counter = Counter(" ".join(m[2].split())[:80] for m in msgs)
        clip_start, clip_end = window or self.clip_window(start, end, peak)
        return {
            "source": source,
            "start": float(start), "end": float(end), "peak": float(peak),
            "score": round(float(score), 1),
            "category": category,
            "clip_start": clip_start, "clip_end": clip_end,
            "breakdown": {**parts, "lag": round(self.lag, 1), "categories": cats, "messages": len(msgs),
                          "weights": {k: round(v, 3) for k, v in self.weights.items()}},
            "chat_sample": [[t, n] for t, n in counter.most_common(15)],
            "filtered": None,
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
