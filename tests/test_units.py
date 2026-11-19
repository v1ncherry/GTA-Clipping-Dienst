import shutil
import subprocess

import pytest

from clipper.config import DEFAULTS, deep_merge
from clipper.layout import x_expression
from clipper.platforms import detect_platform
from clipper.platforms.kick import parse_pusher_message
from clipper.platforms.twitch import parse_irc_line
from clipper.subtitles import ass_color, ass_time, build_ass, chunk_words
from clipper.transcribe import Word, smart_trim
from clipper.web.app import _merge_settings


@pytest.mark.parametrize("url,expected", [
    ("https://www.twitch.tv/Papaplatte", ("twitch", "papaplatte", "https://www.twitch.tv/papaplatte")),
    ("twitch.tv/someone/videos", ("twitch", "someone", "https://www.twitch.tv/someone")),
    ("https://www.youtube.com/@MontanaBlack", ("youtube", "MontanaBlack", "https://www.youtube.com/@MontanaBlack")),
    ("https://youtube.com/channel/UC123", ("youtube", "UC123", "https://www.youtube.com/channel/UC123")),
    ("https://kick.com/Trymacs", ("kick", "trymacs", "https://kick.com/trymacs")),
])
def test_detect_platform(url, expected):
    assert detect_platform(url) == expected


def test_detect_platform_rejects_unknown():
    with pytest.raises(ValueError):
        detect_platform("https://example.com/foo")


def test_twitch_irc_parse():
    line = ("@badge-info=;color=#FF0000;display-name=CoolUser;tmi-sent-ts=1700000000123;user-id=1 "
            ":cooluser!cooluser@cooluser.tmi.twitch.tv PRIVMSG #streamer :KEKW das war krank")
    msg = parse_irc_line(line)
    assert msg.author == "CoolUser"
    assert msg.text == "KEKW das war krank"
    assert abs(msg.ts - 1700000000.123) < 1e-6
    assert parse_irc_line(":tmi.twitch.tv 001 justinfan123 :Welcome") is None


def test_kick_pusher_parse():
    raw = ('{"event":"App\\\\Events\\\\ChatMessageEvent","data":"{\\"content\\":\\"[emote:37226:KEKW] lol\\",'
           '\\"sender\\":{\\"username\\":\\"fan1\\"},\\"created_at\\":\\"2026-11-19T20:00:00+00:00\\"}"}')
    msg = parse_pusher_message(raw)
    assert msg.author == "fan1"
    assert msg.text == "KEKW lol"


def test_ass_helpers():
    assert ass_color("#FFE600") == "&H0000E6FF"
    assert ass_time(3725.456) == "1:02:05.46"


def test_chunking_and_ass():
    words = [Word(0.0, 0.3, "Alter"), Word(0.35, 0.6, "was"), Word(0.65, 1.0, "war"), Word(1.05, 1.4, "das?"),
             Word(2.5, 2.8, "Nein"), Word(2.85, 3.2, "bitte")]
    chunks = chunk_words(words, max_words=3, max_chars=18)
    assert [len(c) for c in chunks] == [3, 1, 2]
    ass = build_ass(words, {"sub_y": 1200, "hook_y": 300, "credit": [30, 100, 7]}, DEFAULTS, 10.0,
                    hook="Er kann nicht mehr 😂", credit="twitch.tv/x")
    assert ass.count("Style: Sub") == 1
    assert "ER KANN NICHT MEHR" in ass and "😂" not in ass.split("[Events]")[1]
    assert "{\\c&H0000E6FF}ALTER" in ass
    assert "twitch.tv/x" in ass


def test_smart_trim_moves_to_sentence_bounds():
    words = [Word(8.0, 8.4, "Okay"), Word(8.5, 9.0, "pass auf."), Word(10.2, 10.5, "Jetzt"), Word(10.6, 11.0, "fahr"),
             Word(11.1, 11.5, "rein!"), Word(40.0, 40.5, "Was"), Word(40.6, 41.4, "war"), Word(41.5, 42.4, "das")]
    start, end = smart_trim(words, 10.6, 41.0, 20, 60)
    assert start == pytest.approx(10.05)     # Satzanfang "Jetzt"
    assert end == pytest.approx(42.75)       # Satzende "das"


def test_x_expression():
    assert x_expression([[0.0, 120]]) == "120"
    expr = x_expression([[0.0, 100], [1.0, 200], [2.0, 200]])
    assert expr.startswith("lt(t,1.00)*(100+100*(t-0.00)/1.00)")
    assert "gte(t,2.00)*200" in expr


def test_merge_settings_whitelist():
    out = _merge_settings({"detection": {"trigger_score": 70}},
                          {"detection": {"trigger_score": None, "max_clips_per_hour": 5, "evil": 1}, "web": {"x": 1}})
    assert out == {"detection": {"max_clips_per_hour": 5}}


def test_deep_merge():
    cfg = deep_merge(DEFAULTS, {"detection": {"trigger_score": 80}})
    assert cfg["detection"]["trigger_score"] == 80
    assert cfg["detection"]["release_score"] == DEFAULTS["detection"]["release_score"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")
def test_render_crop_layout(tmp_path):
    from clipper.config import load_config
    from clipper.layout import plan_layout
    from clipper.render import render_clip
    from clipper.util import probe

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=1280x720:r=30:d=8", "-f", "lavfi", "-i", "sine=d=8", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)
    cfg = load_config(tmp_path / "none.yaml")
    cfg["data_dir"] = str(tmp_path)
    info = probe(cfg, src)
    plan = plan_layout(cfg, str(src), 1.0, 5.0, info, facecam_setting="none")
    assert plan["type"] == "crop"
    ass = tmp_path / "subs.ass"
    ass.write_text(build_ass([Word(0.2, 0.8, "Test")], plan, cfg, 5.0, hook="Hook"), encoding="utf-8")
    out = tmp_path / "out.mp4"
    render_clip(cfg, str(src), 1.0, 5.0, plan, info, str(ass), str(out))
    res = probe(cfg, out)
    assert (res["width"], res["height"]) == (1080, 1920)
    assert 4.5 < res["duration"] < 5.5
    assert res["has_audio"]


def test_texts_follow_streamer_language():
    from clipper.texts import build_hashtags, make_texts, text_language

    streamer = {"name": "Luca", "url": "https://www.twitch.tv/luca", "platform": "twitch"}
    clip = {"id": 3, "category": "funny"}
    en_cfg = deep_merge(DEFAULTS, {"transcription": {"language": "en"}})
    de_cfg = deep_merge(DEFAULTS, {"transcription": {"language": "de"}})
    auto_cfg = deep_merge(DEFAULTS, {"transcription": {"language": "auto"}})
    assert text_language(en_cfg) == "en" and text_language(de_cfg) == "de" and text_language(auto_cfg) == "de"
    en, de = make_texts(en_cfg, streamer, clip), make_texts(de_cfg, streamer, clip)
    german = ("nicht", "Chat ist", "lustig", "Warum", "ESKALIERT", "WIE KANN")
    assert not any(w in en["title"] + en["hook"] for w in german)
    assert "#lustig" not in build_hashtags(en_cfg, streamer, "funny")
    assert "#lustig" in build_hashtags(de_cfg, streamer, "funny")
    assert en["title"] != de["title"]


def test_auto_language_lets_whisper_detect(monkeypatch):
    import clipper.transcribe as tr

    seen = []

    class Fake(tr.Transcriber):
        def transcribe(self, wav, language):
            seen.append(language)
            return []

    monkeypatch.setattr(tr, "_INSTANCE", Fake())
    for lang in ("auto", "", None, "en"):
        tr.transcribe_locked(DEFAULTS, "x.wav", lang)
    assert seen == [None, None, None, "en"]


def test_desktop_login_sets_cookie_and_password_still_protects(tmp_path):
    from fastapi.testclient import TestClient

    from clipper.web.app import create_app

    class Stub:
        current = {}

        class q:  # noqa: N801
            @staticmethod
            def qsize():
                return 0

        def status(self):
            return []

    cfg = deep_merge(DEFAULTS, {"data_dir": str(tmp_path), "web": {"password": "geheim"}})
    (tmp_path / "secrets").mkdir()
    from clipper.db import DB

    db = DB(str(tmp_path / "t.db"))
    app = create_app(cfg, db, Stub(), Stub(), None)
    token = (tmp_path / "secrets" / "desktop.token").read_text().strip()
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/desktop-login?token=falsch", follow_redirects=False).status_code == 403
        r = c.get(f"/desktop-login?token={token}", follow_redirects=False)
        assert r.status_code == 303
        assert c.get("/api/status").status_code == 200  # Cookie aus der Weiterleitung
    with TestClient(app) as c2:
        assert c2.get("/api/info", auth=("x", "geheim")).json()["password"] is True


def test_cleanup_buffer_removes_only_stale_sessions(tmp_path):
    import os
    import time

    from clipper.session import cleanup_buffer

    cfg = deep_merge(DEFAULTS, {"data_dir": str(tmp_path)})
    buf = tmp_path / "buffer"
    old, fresh = buf / "luca" / "20260101-120000", buf / "vice" / "20260101-130000"
    for d in (old, fresh):
        (d / "part0").mkdir(parents=True)
        (d / "part0" / "seg_000001.ts").write_bytes(b"x" * 1000)
    (buf / "calib-luca.jpg").write_bytes(b"jpg")
    past = time.time() - 3600
    for p in [old, *old.rglob("*")]:
        os.utime(p, (past, past))
    removed, freed = cleanup_buffer(cfg)
    assert (removed, freed) == (1, 1000)
    assert not old.exists() and not (buf / "luca").exists()  # leerer Streamer-Ordner gleich mit weg
    assert (fresh / "part0" / "seg_000001.ts").exists()  # wird noch beschrieben -> bleibt
    assert (buf / "calib-luca.jpg").exists()


@pytest.mark.skipif(not __import__("sys").platform.startswith("win"), reason="Windows-Job nur unter Windows")
def test_guarded_children_die_when_clipper_is_killed():
    """Harter Absturz: Kindprozesse aus popen_guarded (auch deren Kinder) dürfen nicht weiterlaufen."""
    import ctypes
    import os
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parent.parent)
    helper_code = (
        "import os, sys, time; sys.path.insert(0, sys.argv[1]);"
        "from clipper.util import popen_guarded, NO_WINDOW;"
        "c = popen_guarded([sys.executable, '-c', 'import time; time.sleep(30)'], creationflags=NO_WINDOW);"
        "print(os.getpid(), c.pid, flush=True); time.sleep(30)"
    )
    helper = subprocess.Popen([sys.executable, "-c", helper_code, root], stdout=subprocess.PIPE, text=True)
    try:
        interp_pid, child_pid = map(int, helper.stdout.readline().split())
        subprocess.run(["taskkill", "/F", "/PID", str(interp_pid)], capture_output=True, check=True)
        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
        if h:  # Prozess existiert noch -> muss innerhalb von 5 s enden
            try:
                assert k32.WaitForSingleObject(ctypes.c_void_p(h), 5000) == 0, "Kindprozess lebt nach dem Kill weiter"
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
    finally:
        helper.kill()
        assert os.getpid()  # nichts weiter aufzuräumen: das Kind stirbt mit dem Job


# --------------------------------------------------------------------------- ClipStream-Erweiterungen

def test_handles_without_link():
    from clipper.platforms import detect_platform

    assert detect_platform("papaplatte") == ("twitch", "papaplatte", "https://www.twitch.tv/papaplatte")
    assert detect_platform("Trymacs", "kick") == ("kick", "trymacs", "https://kick.com/trymacs")
    assert detect_platform("@MontanaBlack")[2] == "https://www.youtube.com/@MontanaBlack"
    assert detect_platform("yt:@Rezo")[0] == "youtube"
    assert detect_platform("kick:abc")[2] == "https://kick.com/abc"


def test_twitch_raid_and_color():
    raid = ("@msg-id=raid;login=bigstreamer;system-msg=1000\\sraiders;tmi-sent-ts=1700000000000 "
            ":tmi.twitch.tv USERNOTICE #streamer")
    msg = parse_irc_line(raid)
    assert msg.kind == "raid" and "1000 raiders" in msg.text
    line = ("@color=#1E90FF;display-name=Fan;tmi-sent-ts=1700000000000 :fan!fan@fan.tmi.twitch.tv "
            "PRIVMSG #streamer :hi")
    assert parse_irc_line(line).color == "#1E90FF"


def test_chat_overlay_events():
    from clipper.subtitles import chat_events

    msgs = [{"t": -5, "author": "early", "text": "vorher"}, {"t": 1.0, "author": "a", "text": "KEKW 😂"},
            {"t": 1.1, "author": "b", "text": "x" * 200, "color": "#FF0000"}, {"t": 2, "author": "c", "text": "😂😂"}]
    ev = chat_events(msgs, {"x": 600, "y": 0, "w": 480, "h": 640, "panel": True}, 10.0)
    assert ev and ev[0].startswith("Dialogue: 3,0:00:00.00")
    assert "vorher" in ev[0] and "…" in ev[-1]
    assert all("😂" not in e for e in ev)           # Emojis kann libass nicht – werden entfernt
    assert "&H000000FF" in ev[-1]                   # Namensfarbe #FF0000 -> ASS-Farbe


def test_settings_validation():
    from clipper.web.app import validate_global_settings

    out = validate_global_settings({"detection.stream_share": 140, "special.categories": ["glitch", "evil"],
                                    "monitor.only_keywords": "gta, fortnite", "render.chat_overlay": 1})
    assert out == {"detection": {"stream_share": 100}, "special": {"categories": ["glitch"]},
                   "monitor": {"only_keywords": ["gta", "fortnite"]}, "render": {"chat_overlay": True}}
    with pytest.raises(ValueError):
        validate_global_settings({"web.password": "x"})


def test_effective_config_layers(tmp_path):
    from clipper.config import effective_config
    from clipper.db import DB

    db = DB(str(tmp_path / "t.db"))
    db.set_setting("global", {"detection": {"stream_share": 60}})
    cfg = effective_config(DEFAULTS, db, {"settings": {"detection": {"trigger_score": 80}}})
    assert cfg["detection"]["stream_share"] == 60 and cfg["detection"]["trigger_score"] == 80
    assert cfg["detection"]["release_score"] == DEFAULTS["detection"]["release_score"]


def test_question_detection_in_transcript():
    from clipper.processing import asked_chat_question

    ws = [Word(10, 10.3, "Chat,"), Word(10.4, 10.7, "soll"), Word(10.8, 11, "ich"), Word(11.1, 11.5, "springen?")]
    assert asked_chat_question(ws, 9, 12)
    assert not asked_chat_question([Word(10, 11, "Alter"), Word(11, 12, "krass!")], 9, 12)


def test_custom_keyword_special_moment():
    import random as _r

    from clipper.config import keyword_spec
    from clipper.detector import Detector
    from clipper.signals import Timeline
    from clipper.texts import template_texts

    cfg = deep_merge(DEFAULTS, {})
    tl = Timeline(keyword_spec(cfg))
    rng = _r.Random(2)
    for s in range(900):
        for _ in range(2):
            tl.add_chat(s + rng.random(), f"u{rng.randint(1, 300)}", rng.choice(["hallo", "gg", "nice", "wie gehts"]))
        for q in range(4):
            tl.add_audio(s + q * 0.25, -30 + rng.gauss(0, 2))
        tl.add_motion(s, 0.03)
    for s in range(605, 612):
        tl.add_chat(s + 0.3, f"fan{s}", "STRIPCLUB 💀")
        tl.add_chat(s + 0.6, f"fanb{s}", "er geht in den strip club")
    events = []
    det = Detector(tl, cfg, lag=5, on_event=events.append)
    det.advance(900)
    det.finalize()
    kw = [e for e in events if e["category"] == "stichwort"]
    assert len(kw) == 1 and kw[0]["breakdown"]["keyword"] in ("stripclub", "strip club")
    t = template_texts({"name": "Papaplatte"}, "stichwort", 1, keyword=kw[0]["breakdown"]["keyword"])
    assert "Papaplatte" in t["title"] and "Strip" in t["title"]


def test_learning_from_discards(tmp_path):
    from clipper.db import DB
    from clipper.learn import last_note, record

    db = DB(str(tmp_path / "l.db"))
    sid = db.add_streamer("S", "https://www.twitch.tv/s", "twitch", "s")
    cfg = deep_merge(DEFAULTS, {})
    notes = []
    for i in range(3):
        cid = db.add_clip(streamer_id=sid, source="auto", dir=str(tmp_path))
        notes.append(record(cfg, db, db.get_clip(cid), "bad", "spam"))
    assert notes[:2] == [None, None] and notes[2]
    det = db.get_streamer(sid)["settings"]["detection"]
    assert det["stream_share"] == DEFAULTS["detection"]["stream_share"] + 5
    assert det["trigger_score"] == DEFAULTS["detection"]["trigger_score"] + 1
    assert last_note(db, sid)["changes"]["stream_share"] == det["stream_share"]
    # danach wird neu gezählt: ein weiterer Spam-Clip ändert nichts
    cid = db.add_clip(streamer_id=sid, source="auto", dir=str(tmp_path))
    assert record(cfg, db, db.get_clip(cid), "bad", "spam") is None


def test_tiktok_long_window():
    from clipper.processing import long_window

    cfg = deep_merge(DEFAULTS, {})
    ls, le = long_window(cfg, 100.0, 140.0, 300.0)
    assert 62 <= le - ls <= 90 and ls < 100 and le >= 140
    assert long_window(cfg, 0.0, 30.0, 40.0) is None             # zu wenig Material
    ls, le = long_window(cfg, 5.0, 45.0, 200.0)                  # vorne kaum Platz -> hinten verlängern
    assert ls == 0.0 and le - ls >= 62
    assert long_window(deep_merge(DEFAULTS, {"render": {"tiktok_long": False}}), 100, 140, 300) is None


def test_notifications_and_autopilot(tmp_path, monkeypatch):
    import http.server
    import json as _json
    import threading as _t

    from clipper.db import DB
    from clipper.notify import Autopilot, send_sync

    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            got.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    hook = f"http://127.0.0.1:{srv.server_port}/api/webhooks/x"
    cfg = deep_merge(DEFAULTS, {"notify": {"discord_webhook": hook}})
    assert send_sync(cfg, "Hallo") == []
    assert _json.loads(got[-1])["content"] == "Hallo"

    db = DB(str(tmp_path / "a.db"))
    sid = db.add_streamer("S", "https://www.twitch.tv/s", "twitch", "s")
    aid = db.add_account("tiktok", "clips", {})
    db.set_setting("global", {"autopilot": {"enabled": True, "min_score": 90}, "notify": {"discord_webhook": hook}})
    calls = []

    class FakeUploader:
        def upload_clip(self, cid, ids):
            calls.append((cid, ids))

    ap = Autopilot(DEFAULTS, db, FakeUploader(), "http://pc:8686")
    strong = db.add_clip(streamer_id=sid, source="auto", score=96, title="Krass", status="ready")
    weak = db.add_clip(streamer_id=sid, source="auto", score=80, title="Naja", status="ready")
    ap.clip_ready(strong)
    ap.clip_ready(weak)
    ap.clip_ready(strong, first=False)          # Neu rendern: keine zweite Meldung/kein zweiter Upload
    assert calls == [(strong, [aid])]
    import time as _time
    _time.sleep(0.5)
    texts = [_json.loads(g)["content"] for g in got[1:]]
    assert any("automatisch hochgeladen" in t and "Krass" in t for t in texts)
    assert any("Naja" in t for t in texts) and len(texts) == 2
    srv.shutdown()


def test_ollama_autodetect_and_pull():
    import time as _time

    from clipper import llm
    from fake_ollama import FakeOllama

    fake = FakeOllama(models=["nomic-embed-text:latest", "llama3.1:8b"])
    try:
        cfg = deep_merge(DEFAULTS, {"llm": {"url": fake.url}})
        llm._cache["t"] = 0
        assert llm.mode(cfg) == "auto"
        assert llm.mode(deep_merge(DEFAULTS, {"llm": {"enabled": False, "mode": None}})) == "auto"  # alte config.yaml
        assert llm.active_model(cfg) == "llama3.1:8b"            # gewünschtes fehlt -> bevorzugtes installiertes
        assert llm.active_model(deep_merge(cfg, {"llm": {"mode": "off"}})) is None
        llm.start_pull(cfg, "qwen2.5:7b-instruct")
        for _ in range(50):
            if llm.pull_status()["done"]:
                break
            _time.sleep(0.05)
        assert llm.pull_status()["status"] == "Fertig"
        assert llm.active_model(cfg) == "qwen2.5:7b-instruct"    # nach dem Laden wird das empfohlene genommen
    finally:
        fake.close()
        llm._cache["t"] = 0


def test_llm_titles_and_weak_moment_filter(tmp_path):
    from clipper import llm
    from clipper.texts import make_texts
    from fake_ollama import FakeOllama

    fake = FakeOllama(models=["qwen2.5:7b-instruct"], score=2)
    try:
        cfg = deep_merge(DEFAULTS, {"llm": {"url": fake.url}})
        llm._cache["t"] = 0
        t = make_texts(cfg, {"name": "S", "url": "https://www.twitch.tv/s", "platform": "twitch"},
                       {"id": 1, "category": "funny", "transcript": "nein!", "chat_sample": [["KEKW", 5]]})
        assert t["title"] == "Er fährt direkt in den Pool 😂" and t["llm_score"] == 2
        assert "#pool" in t["hashtags"]
        assert fake.requests[-1]["model"] == "qwen2.5:7b-instruct" and fake.requests[-1]["format"] == "json"
    finally:
        fake.close()
        llm._cache["t"] = 0


def test_llm_unreachable_falls_back_to_templates():
    from clipper import llm
    from clipper.texts import make_texts

    cfg = deep_merge(DEFAULTS, {"llm": {"url": "http://127.0.0.1:9"}})
    llm._cache["t"] = 0
    t = make_texts(cfg, {"name": "S", "url": "https://www.twitch.tv/s", "platform": "twitch"},
                   {"id": 1, "category": "funny"})
    assert t["title"] and t["llm_score"] is None
    llm._cache["t"] = 0
