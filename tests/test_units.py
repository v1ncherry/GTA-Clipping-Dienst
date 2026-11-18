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
