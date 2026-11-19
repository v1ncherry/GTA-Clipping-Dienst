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


# --------------------------------------------------------------------------- intelligentere Erkennung

def _burst(tl, start, seconds, per_sec, texts, seed=11):
    rng = random.Random(seed)
    for s in range(start, start + seconds):
        for _ in range(per_sec):
            tl.add_chat(s + rng.random(), f"fan{rng.randint(1, 5000)}", rng.choice(texts))


def _run(tl, cfg, seconds, lag=5):
    events = []
    det = Detector(tl, cfg, lag=lag, on_event=events.append)
    det.advance(seconds)
    det.finalize()
    return events


def test_question_answers_are_filtered():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 900)
    # Streamer fragt "Welches Auto soll ich nehmen?" -> Chat antwortet, keine Reaktion im Ton
    _burst(tl, 605, 15, 25, ["ja", "nein", "2", "1", "links", "Lambo", "das rote", "ja", "nein"])
    events = _run(tl, cfg, 900)
    assert len(events) == 1
    assert events[0]["filtered"] == "Chat beantwortet eine Frage"
    assert events[0]["quality"]["answer_ratio"] >= 0.45


def test_real_hype_is_not_filtered():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 900)
    _burst(tl, 605, 15, 25, ["KEKW", "LMAO", "😂😂", "ICANT", "ich kann nicht mehr"])
    events = _run(tl, cfg, 900)
    assert len(events) == 1 and events[0]["filtered"] is None


def test_raid_burst_is_filtered():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 900)
    tl.add_noise(603, "raid")
    _burst(tl, 605, 15, 25, ["KEKW raid", "PogChamp", "🔥🔥", "hallo aus dem raid"])
    events = _run(tl, cfg, 900)
    assert len(events) == 1 and events[0]["filtered"] == "Raid/Sub-Welle im Chat"


def test_bot_commands_and_bots_are_ignored():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"], cfg["chat_filter"])
    _fill_baseline(tl, 900)
    _burst(tl, 605, 15, 30, ["!giveaway", "!join", "!vote 2"])
    for s in range(605, 620):
        tl.add_chat(s, "Nightbot", "Folgt auf Instagram!")
    assert _run(tl, cfg, 900) == []


def test_easter_egg_mentions_create_special_clip():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 900)
    _burst(tl, 605, 8, 2, ["easter egg!!", "das ist ein easter egg", "secret entdeckt", "lore referenz"])
    events = _run(tl, cfg, 900)
    assert len(events) == 1
    ev = events[0]
    assert ev["source"] == "special" and ev["category"] == "easteregg" and ev["filtered"] is None
    assert ev["clip_start"] < 600 < ev["clip_end"]


def test_sudden_silence_creates_tension_clip():
    cfg = _cfg()
    tl = Timeline(cfg["keywords"])
    _fill_baseline(tl, 600, rate=3.0)
    # Chat wird 30 s fast still (alle schauen gebannt), danach wieder normal
    rng = random.Random(4)
    nrng = np.random.default_rng(4)
    for s in range(600, 900):
        quiet = 640 <= s < 670
        n = (1 if s % 6 == 0 else 0) if quiet else nrng.poisson(3.0)
        for _ in range(n):
            tl.add_chat(s + rng.random(), f"u{rng.randint(1, 400)}", "hallo")
        for q in range(4):
            tl.add_audio(s + q * 0.25, -30 + nrng.normal(0, 2.5))
        tl.add_motion(s, 0.03)
    events = _run(tl, cfg, 900)
    tension = [e for e in events if e["category"] == "spannung"]
    assert len(tension) == 1
    assert tension[0]["clip_start"] <= 640 and tension[0]["clip_end"] >= 660


def test_stream_share_slider():
    from clipper.detector import effective_weights

    d = dict(DEFAULTS["detection"])
    w, solo = effective_weights({**d, "stream_share": 0})
    assert w["audio"] == 0 and w["motion"] == 0 and solo == 0
    assert abs(sum(w.values()) - 1) < 1e-9
    w, solo = effective_weights({**d, "stream_share": 100})
    assert w["chat_rate"] == 0 and abs(w["audio"] + w["motion"] - 1) < 1e-9 and solo > 0.85
