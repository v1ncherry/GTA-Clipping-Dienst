"""Konfiguration: Defaults + config.yaml + Overrides pro Streamer."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "data_dir": "data",
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "workers": 2,
    "web": {
        "host": "0.0.0.0",
        "port": 8686,
        # Leer = kein Passwort. Wenn das Dashboard im Netzwerk erreichbar ist, unbedingt setzen!
        "password": "",
    },
    "monitor": {
        "poll_seconds": 60,
        # Nur clippen, wenn Kategorie oder Stream-Titel eines dieser Wörter enthält.
        # Leere Liste = alles clippen.
        "only_keywords": ["gta", "grand theft auto"],
        "quality": "1080p60,1080p,936p60,900p60,720p60,720p,best",
        # streamlink (empfohlen) oder yt-dlp
        "capture_tool": "streamlink",
        "kick_pusher_key": "32cbd69e4b950bf97679",
    },
    "buffer": {
        "segment_seconds": 4,
        "keep_minutes": 15,
    },
    "detection": {
        # Hype-Score 0-100. Ab trigger_score startet ein Moment, unter release_score endet er.
        "trigger_score": 72,
        "release_score": 50,
        "min_event_seconds": 2,
        "max_event_seconds": 45,
        "cooldown_seconds": 45,
        "max_clips_per_hour": 10,
        # Wenn das Stundenlimit erreicht ist, kommen nur noch Momente über diesem Score durch.
        "over_limit_score": 90,
        "baseline_minutes": 10,
        "warmup_minutes": 3,
        # Wie viele Sekunden der Chat dem Bild hinterherhinkt (wird bei auto_lag nachjustiert).
        "chat_delay": {"twitch": 4, "youtube": 7, "kick": 5, "local": 1, "vod": 8},
        "auto_lag": True,
        # Gewichte des kombinierten Scores (Chat + Stream).
        "weights": {"chat_rate": 0.35, "chatters": 0.15, "emotes": 0.25, "audio": 0.15, "motion": 0.10},
        # "Stream-Moment" unabhängig vom Chat (Schreien, Explosionen, Action).
        "solo_weights": {"audio": 0.75, "motion": 0.25},
        "solo_factor": 0.85,
        "clip_request_bonus": 0.6,
    },
    "clips": {
        "min_seconds": 20,
        "max_seconds": 60,
        "pre_roll": 18,
        "post_roll": 5,
        # Schnitt an Satzgrenzen ausrichten (braucht Transkription).
        "smart_trim": True,
    },
    "keywords": {
        "funny": {
            "weight": 1.0,
            "tokens": ["KEKW", "LUL", "LULW", "OMEGALUL", "ICANT", "LMAO", "LMFAO", "LOL", "XD", "XDD",
                       "HAHA", "HAHAHA", "😂", "🤣", "😹", "lachflash", "ich kann nicht", "kann nicht mehr",
                       "pepeLaugh", "KEKL", "ehrenlos"],
        },
        "hype": {
            "weight": 1.0,
            "tokens": ["POG", "POGGERS", "PogChamp", "PogU", "W", "WW", "WWW", "🔥", "GOAT", "krass", "insane",
                       "geisteskrank", "heftig", "sheesh", "LETSGO", "lets go", "let's go", "EZ", "Clap", "🐐"],
        },
        "shock": {
            "weight": 1.0,
            "tokens": ["WTF", "OMG", "monkaS", "monkaW", "D:", "😳", "😱", "🤯", "digga", "alter", "junge",
                       "NAHH", "nahhh", "was zur", "wie bitte", "?!"],
        },
        "fail": {
            "weight": 0.9,
            "tokens": ["L", "LL", "RIP", "F", "Sadge", "bruh", "peinlich", "💀", "😭", "ratio", "unlucky"],
        },
        "clip": {
            "weight": 2.0,
            "tokens": ["clip", "clipt", "clippen", "clip it", "clip that", "!clip", "clipped", "clipper"],
        },
    },
    "transcription": {
        # auto | faster-whisper | mlx | whisper.cpp | none
        "backend": "auto",
        "model": "large-v3-turbo",
        "language": "de",
        "device": "auto",
        "compute_type": "int8",
        "mlx_model": "mlx-community/whisper-large-v3-turbo",
        "whispercpp_bin": "",
        "whispercpp_model": "",
    },
    "render": {
        "width": 1080,
        "height": 1920,
        "fps": "auto",
        # auto | h264_amf | h264_videotoolbox | h264_nvenc | h264_qsv | libx264
        "encoder": "auto",
        "video_bitrate": "12M",
        # auto (Facecam oben, sonst Smart-Crop) | stack | crop | blur
        "layout": "auto",
        "loudnorm": True,
        "hook_text": True,
        "hook_seconds": 3.5,
        "credit": True,
        "subtitles": {
            "enabled": True,
            "font": "Arial Black",
            "fonts_dir": "",
            "size": 82,
            "uppercase": True,
            "max_words": 3,
            "max_chars": 18,
            "color": "#FFFFFF",
            "highlight": "#FFE600",
            "outline": 7,
            # auto oder Anteil der Bildhöhe (0.0 - 1.0)
            "position": "auto",
        },
    },
    "llm": {
        # Lokales LLM über Ollama (kostenlos). Ohne LLM werden Titel aus Vorlagen gebaut.
        "enabled": False,
        "url": "http://localhost:11434",
        "model": "qwen2.5:7b-instruct",
        # Clips mit LLM-Bewertung darunter werden als "schwach" markiert (0 = aus).
        "min_score": 0,
        "timeout": 120,
    },
    "hashtags": ["#gta6", "#gtavi", "#gta", "#gaming"],
    "uploads": {
        "youtube": {
            "client_secrets": "client_secret.json",
            "privacy": "public",
            "category_id": "20",
        },
        "tiktok": {
            "client_key": "",
            "client_secret": "",
            "redirect_uri": "",
            # inbox = landet als Entwurf in der TikTok-App (funktioniert ohne App-Audit),
            # direct = wird direkt gepostet (öffentlich erst nach TikTok-Audit)
            "mode": "inbox",
            "privacy": "PUBLIC_TO_EVERYONE",
        },
        "instagram": {
            "api_base": "https://graph.instagram.com",
            "api_version": "v21.0",
            "share_to_feed": True,
        },
    },
}


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(path: str | os.PathLike | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    candidates = [path] if path else ["config.yaml", "config.yml"]
    for cand in candidates:
        if cand and Path(cand).exists():
            with open(cand, encoding="utf-8") as fh:
                user = yaml.safe_load(fh) or {}
            cfg = deep_merge(cfg, user)
            cfg["_config_path"] = str(Path(cand).resolve())
            break
    data_dir = Path(cfg["data_dir"]).expanduser()
    if not data_dir.is_absolute():
        base = Path(cfg.get("_config_path") or path or "config.yaml").resolve().parent
        data_dir = base / data_dir
    cfg["data_dir"] = str(data_dir)
    for sub in ("buffer", "clips", "models", "logs", "vods", "secrets"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    return cfg


def streamer_config(cfg: dict, streamer: dict | None) -> dict:
    """Globale Config mit den Overrides eines Streamers (streamer.settings) zusammenführen."""
    if not streamer:
        return cfg
    return deep_merge(cfg, streamer.get("settings") or {})


def data_path(cfg: dict, *parts: str) -> Path:
    return Path(cfg["data_dir"]).joinpath(*parts)
