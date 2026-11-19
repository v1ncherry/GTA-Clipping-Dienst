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
        # Nur clippen, wenn Kategorie oder Stream-Titel eines dieser Wörter enthält (z.B. ["gta"]).
        # Leere Liste = alles clippen.
        "only_keywords": [],
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
        # Hauptregler: Anteil des Streams (Lautstärke + Bewegung) am Score in Prozent, Rest = Chat.
        # 0 = nur Chat, 100 = nur Stream-Reaktion.
        "stream_share": 40,
        # Feinregler: Gewichte innerhalb der Gruppen Chat (chat_rate, chatters, emotes) und Stream (audio, motion).
        "weights": {"chat_rate": 0.35, "chatters": 0.15, "emotes": 0.25, "audio": 0.15, "motion": 0.10},
        # "Stream-Moment" unabhängig vom Chat (Schreien, Explosionen, Action).
        "solo_weights": {"audio": 0.75, "motion": 0.25},
        "solo_factor": 0.85,
        "clip_request_bonus": 0.6,
    },
    # Filter gegen "leere" Chat-Ausbrüche. Aussortierte Momente werden nicht gerendert, sind aber im Dashboard
    # unter "Aussortiert" sichtbar und lassen sich per Klick doch rendern.
    "filters": {
        "enabled": True,
        # Chat beantwortet eine Frage: viele kurze Antworten (ja/nein/Zahlen/Namen), kaum Emotes/Lachen
        "max_answer_ratio": 0.38,
        # Ein Chat-Ausbruch braucht Emotes/Lachen/Schock (Anteil) ODER eine Reaktion im Stream (z-Wert Ton/Bild)
        "min_hype_ratio": 0.30,
        "min_stream_reaction": 1.2,
        # So viele Sekunden nach einem Raid / einer Sub-Geschenkwelle wird nicht geclippt
        "raid_seconds": 90,
        # Nach dem Transkribieren: Hat der Streamer kurz vorher den Chat etwas gefragt?
        "question_check": True,
        # Streamer schläft / ist AFK: Chat schreibt in den letzten Minuten auffällig oft "wake up", "schläft er?" …
        "sleep_check": True,
        "sleep_window": 180,     # so viele Sekunden Chat vor dem Moment werden angeschaut
        "sleep_min_msgs": 3,     # mind. so viele Schlaf-Nachrichten …
        "sleep_ratio": 0.04,     # … und mind. dieser Anteil am Chat
        # Nach dem Transkribieren: Ist der Moment nur eine vorgelesene Spende / TTS / Super Chat / Bits-Ansage?
        "alert_check": True,
        "alert_max_other_words": 12,   # mehr eigene Worte des Streamers drumherum = echte Reaktion, bleibt drin
        # Aussortierte Rohvideos nach so vielen Stunden löschen
        "keep_hours": 48,
    },
    # Spezial-Momente, die auch ohne großen Hype geclippt werden
    "special": {
        "enabled": True,
        # Chat erwähnt Easter Eggs / Geheimnisse, Glitches / Bugs oder eigene Stichwörter auffällig oft
        "categories": ["easteregg", "glitch", "stichwort"],
        # Eigene Stichwörter (Orte, Insider, Namen …) – fallen sie gehäuft im Chat, wird geclippt
        "keywords": ["stripclub", "strip club", "stripper"],
        "min_mentions": 4,
        "min_z": 3.0,
        # Chat wird plötzlich still/gebannt (Story, Spannung), obwohl er sonst aktiv ist
        "silence": True,
        "silence_min_baseline": 8,      # mind. so viele Nachrichten pro 5 s im Normalfall
        "silence_ratio": 0.25,          # Stille = unter 25 % des normalen Chat-Tempos
        "silence_seconds": 20,
        "cooldown_seconds": 90,
        "max_per_hour": 4,
    },
    # Autopilot: sehr starke Clips automatisch auf die Standard-Upload-Ziele des Streamers hochladen
    "autopilot": {"enabled": False, "min_score": 95,
                  # zusätzlich: KI-Bewertung mindestens so hoch (nur wenn die KI aktiv ist)
                  "min_llm_score": 6},
    # Benachrichtigungen aufs Handy: Discord-Webhook und/oder Telegram-Bot
    "notify": {
        "discord_webhook": "",
        "telegram_token": "",
        "telegram_chat_id": "",
        "min_score": 0,          # nur Clips ab diesem Score melden
        "on_ready": True,        # neuer Clip fertig
        "on_upload": True,       # Upload erfolgreich / fehlgeschlagen
        "public_url": "",        # Link in der Nachricht, z.B. Tailscale-Adresse des Dashboards
    },
    # Aus Feedback lernen (Verwerfen mit Grund / Hochladen) und Streamer automatisch nachjustieren
    "learn": {"enabled": True},
    "chat_filter": {
        # Bot-Befehle (!giveaway, !vote …) ignorieren; "!clip" zählt weiter als Clip-Ruf
        "ignore_commands": True,
        "bots": ["nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot", "botrixoficial",
                 "kickbot", "sery_bot", "soundalerts", "pokemoncommunitygame", "streamstickers", "commanderroot",
                 "botisimo"],
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
        "easteregg": {
            "weight": 1.0,
            "tokens": ["easter egg", "eastereg", "easteregg", "easter eggs", "secret", "geheim", "geheimnis",
                       "referenz", "reference", "anspielung", "lore", "hidden", "versteckt", "wusste ich nicht",
                       "wie hast du das gefunden", "ufo", "alien", "aliens", "bigfoot", "ghost", "geist"],
        },
        "glitch": {
            "weight": 0.9,
            "tokens": ["glitch", "glitched", "bug", "buggy", "verbuggt", "gebuggt", "physics", "physik", "tpose",
                       "t-pose", "durch die wand", "unter die map", "kaputt", "broken", "was macht der",
                       "was macht die", "npc ai", "rockstar moment"],
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
        # auto (Facecam oben, sonst Smart-Crop) | stack | stack_chat | crop | crop_chat | blur
        "layout": "auto",
        # Größe der Facecam oben: s (25 %), m (33 %), l (42 % der Höhe)
        "cam_size": "m",
        # Chat ins Video einblenden (bei "auto": neben der Facecam bzw. über dem Gameplay)
        "chat_overlay": False,
        # Zusätzliche, längere Fassung für TikTok (über 1 Minute – nur solche Videos werden vergütet)
        "tiktok_long": True,
        "long_seconds": 70,
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
        # Lokales LLM über Ollama (kostenlos) für Titel, Hook und eine zweite Bewertung.
        # auto = benutzen, sobald Ollama läuft und ein Modell installiert ist | on | off
        "mode": "auto",
        "url": "http://localhost:11434",
        # Bevorzugtes Modell; ist es nicht installiert, wird ein anderes installiertes genommen
        "model": "qwen2.5:7b-instruct",
        # Momente, die die KI schlechter als das bewertet (1-10), werden aussortiert (0 = aus)
        "min_score": 3,
        "timeout": 120,
    },
    "hashtags": ["#gaming", "#clips"],
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


def keyword_spec(cfg: dict) -> dict:
    """Chat-Kategorien inkl. der eigenen Stichwörter aus den Einstellungen (Kategorie "stichwort")."""
    spec = dict(cfg["keywords"])
    words = [w for w in (cfg.get("special") or {}).get("keywords") or [] if str(w).strip()]
    if words:
        spec["stichwort"] = {"weight": 1.0, "tokens": words}
    return spec


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


def effective_config(cfg: dict, db=None, streamer: dict | None = None) -> dict:
    """config.yaml + Einstellungen aus dem Dashboard (DB) + Overrides des Streamers."""
    out = cfg
    if db is not None:
        overrides = db.get_setting("global")
        if overrides:
            out = deep_merge(out, overrides)
    return streamer_config(out, streamer)


def streamer_config(cfg: dict, streamer: dict | None) -> dict:
    """Globale Config mit den Overrides eines Streamers (streamer.settings) zusammenführen."""
    if not streamer:
        return cfg
    return deep_merge(cfg, streamer.get("settings") or {})


def data_path(cfg: dict, *parts: str) -> Path:
    return Path(cfg["data_dir"]).joinpath(*parts)
