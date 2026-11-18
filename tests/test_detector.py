import random

import numpy as np

from clipper.config import DEFAULTS, deep_merge
from clipper.detector import Detector
from clipper.signals import KeywordMatcher, Timeline


def _cfg(**detection):
    return deep_merge(DEFAULTS, {"detection": detection})


def _fill_baseline(tl, seconds, rate=2.0, seed=1):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    normal = ["hallo", "was geht", "gg", "wie lange streamst du heute", "nice", "lol", "hi chat", "welches auto ist das"]
    for s in range(seconds):
        for _ in range(nrng.poisson(rate)):
            tl.add_chat(s + rng.random(), f"user{rng.randint(1, 400)}", rng.choice(normal))
        for q in range(4):
            tl.add_audio(s + q * 0.25, -30 + nrng.normal(0, 2.5))
        tl.add_motion(s, 0.03 + abs(nrng.normal(0, 0.005)))


def test_keyword_matcher():
    m = KeywordMatcher(DEFAULTS["keywords"])
    assert "funny" in m.match("KEKW KEKW")
    assert "funny" in m.match("hahahaha")
    assert "clip" in m.match("CLIP IT")
    assert "hype" in m.match("W")
    assert "hype" not in m.match("W was geht")          # "W" nur als ganze Nachricht
    assert "fail" not in m.match("Lol das ist lustig")  # kein falsches "L"
    assert "shock" in m.match("?????")


def test_chat_burst_triggers_event_at_right_time():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 900)
    # Moment bei t=600: Streamer schreit, 5s später explodiert der Chat (Lag 5s)
    for s in range(600, 606):
        for q in range(4):
            tl.add_audio(s + q * 0.25, -8)
    rng = random.Random(7)
    for s in range(605, 625):
        for _ in range(25):
            tl.add_chat(s + rng.random(), f"fan{rng.randint(1, 3000)}", rng.choice(["KEKW", "KEKW KEKW", "LMAO", "😂😂", "clip it"]))
    events = []
    det = Detector(tl, cfg, lag=5, on_event=events.append)
    det.advance(900)
    det.finalize()
    assert len(events) == 1, events
    ev = events[0]
    assert 595 <= ev["peak"] <= 622
    assert ev["category"] == "funny"
    assert ev["clip_start"] < 600 < ev["clip_end"]
    assert cfg["clips"]["min_seconds"] <= ev["clip_end"] - ev["clip_start"] <= cfg["clips"]["max_seconds"]
    assert ev["score"] >= cfg["detection"]["trigger_score"]


def test_quiet_stream_has_no_events():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 1800, seed=3)
    events = []
    det = Detector(tl, cfg, lag=5, on_event=events.append)
    det.advance(1800)
    det.finalize()
    assert events == []


def test_stream_only_moment_without_chat():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 900, rate=0.0)
    for s in range(500, 506):
        for q in range(4):
            tl.add_audio(s + q * 0.25, 0)
        tl.add_motion(s, 0.25)
    events = []
    det = Detector(tl, cfg, lag=5, on_event=events.append)
    det.advance(900)
    det.finalize()
    assert len(events) == 1
    assert events[0]["breakdown"]["mode"] == "stream"
    assert events[0]["category"] == "action"


def test_lag_estimation():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    nrng = np.random.default_rng(5)
    for s in range(1500):
        loud = s % 97 < 4
        for q in range(4):
            tl.add_audio(s + q * 0.25, (-10 if loud else -30) + nrng.normal(0, 1))
        rate = 12 if (s - 6) % 97 < 6 else 1
        for _ in range(nrng.poisson(rate)):
            tl.add_chat(s + 0.5, f"u{nrng.integers(0, 500)}", "hi")
    det = Detector(tl, cfg, lag=0)
    lag = det.estimate_lag(1400)
    assert lag is not None and 5 <= lag <= 9
