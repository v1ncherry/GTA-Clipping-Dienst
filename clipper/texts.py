"""Titel, Hook-Text, Caption und Hashtags – aus Vorlagen oder per lokalem LLM (Ollama, kostenlos)."""

from __future__ import annotations

import json
import logging
import random
import re

import requests

from .util import slugify

log = logging.getLogger("clipper.texts")

TITLES = {
    "funny": ["{n} kann nicht mehr 😂", "Der Chat ist komplett eskaliert 😂", "{n} in GTA 6 ist einfach zu lustig 💀",
              "Warum passiert sowas nur {n}? 😂"],
    "hype": ["{n} mit dem krassesten Move in GTA 6 🔥", "Der Chat dreht komplett durch 🔥",
             "Niemand hat damit gerechnet 🤯"],
    "shock": ["Was ist da gerade passiert?! 😳", "{n} ist sprachlos 💀", "Damit hat {n} nicht gerechnet 😱"],
    "fail": ["{n} hat es komplett verkackt 💀", "GTA 6 Fail des Tages 😭", "Das ging richtig schief 😭"],
    "action": ["Die verrückteste Szene in GTA 6 🔥", "{n} eskaliert in GTA 6 💥", "GTA 6 ist einfach verrückt 🤯"],
}
HOOKS = {
    "funny": ["ER KANN NICHT MEHR", "CHAT ESKALIERT", "WIE KANN DAS SEIN"],
    "hype": ["WAS FÜR EIN MOVE", "CHAT DREHT DURCH", "DAS WAR KRANK"],
    "shock": ["WAS IST DA PASSIERT?!", "WARTE AUF DAS ENDE", "DAMIT RECHNET KEINER"],
    "fail": ["DAS GING SCHIEF", "WARTE AUF DAS ENDE", "KOMPLETT VERKACKT"],
    "action": ["GTA 6 IST VERRÜCKT", "WARTE AUF DAS ENDE", "DAS MUSST DU SEHEN"],
}
CATEGORY_TAGS = {
    "funny": ["#funny", "#lustig"], "hype": ["#viral"], "shock": ["#wtf"], "fail": ["#fail"], "action": ["#action"],
}

# Englischsprachige Streamer (Sprache „en“): englische Titel, Hooks und Hashtags passend zu den Untertiteln
TITLES_EN = {
    "funny": ["{n} can't take it anymore 😂", "Chat lost it completely 😂", "{n} in GTA 6 is way too funny 💀",
              "Why does this only happen to {n}? 😂"],
    "hype": ["{n} with the craziest move in GTA 6 🔥", "Chat is going absolutely crazy 🔥", "Nobody saw that coming 🤯"],
    "shock": ["What just happened?! 😳", "{n} is speechless 💀", "{n} did NOT expect that 😱"],
    "fail": ["{n} completely messed up 💀", "GTA 6 fail of the day 😭", "That went horribly wrong 😭"],
    "action": ["The craziest scene in GTA 6 🔥", "{n} goes crazy in GTA 6 💥", "GTA 6 is just insane 🤯"],
}
HOOKS_EN = {
    "funny": ["HE CAN'T TAKE IT", "CHAT LOST IT", "HOW IS THAT POSSIBLE"],
    "hype": ["WHAT A MOVE", "CHAT GOES CRAZY", "THAT WAS INSANE"],
    "shock": ["WHAT JUST HAPPENED?!", "WAIT FOR THE END", "NOBODY SAW THIS COMING"],
    "fail": ["THAT WENT WRONG", "WAIT FOR THE END", "TOTAL DISASTER"],
    "action": ["GTA 6 IS INSANE", "WAIT FOR THE END", "YOU HAVE TO SEE THIS"],
}
CATEGORY_TAGS_EN = {
    "funny": ["#funny"], "hype": ["#viral"], "shock": ["#wtf"], "fail": ["#fail"], "action": ["#action"],
}


def text_language(cfg: dict) -> str:
    """Sprache für Titel/Hook/Hashtags: Englisch nur, wenn der Streamer auf Englisch steht, sonst Deutsch."""
    return "en" if str(cfg.get("transcription", {}).get("language") or "").lower() == "en" else "de"


def credit_line(streamer: dict) -> str:
    url = re.sub(r"^https?://(www\.)?", "", streamer.get("url", "")).rstrip("/")
    return url if streamer.get("platform") != "local" else streamer.get("name", "")


def build_hashtags(cfg: dict, streamer: dict, category: str, extra: list[str] | None = None) -> list[str]:
    tags = list(cfg.get("hashtags", []))
    tags.append("#" + slugify(streamer.get("name", ""), 30).replace("-", ""))
    tags += (CATEGORY_TAGS_EN if text_language(cfg) == "en" else CATEGORY_TAGS).get(category, [])
    for t in extra or []:
        t = "#" + re.sub(r"[^\wäöüÄÖÜß]", "", str(t).lstrip("#"))
        if len(t) > 1:
            tags.append(t)
    tags.append("#fyp")
    seen, out = set(), []
    for t in tags:
        if t.lower() not in seen and len(t) > 1:
            seen.add(t.lower())
            out.append(t)
    return out[:12]


def build_caption(title: str, hashtags: list[str], streamer: dict) -> str:
    return f"{title}\n\n🎥 {credit_line(streamer)}\n\n{' '.join(hashtags)}"


def template_texts(streamer: dict, category: str, seed: int, lang: str = "de") -> dict:
    rng = random.Random(seed)
    titles, hooks = (TITLES_EN, HOOKS_EN) if lang == "en" else (TITLES, HOOKS)
    cat = category if category in titles else "hype"
    name = streamer.get("name", "He" if lang == "en" else "Er")
    return {"title": rng.choice(titles[cat]).format(n=name), "hook": rng.choice(hooks[cat])}


SYSTEM_PROMPT = """Du bist Social-Media-Editor für virale GTA-6-Shorts (TikTok, YouTube Shorts, Instagram Reels).
Du bekommst das Transkript eines Stream-Ausschnitts und die häufigsten Chat-Nachrichten in dem Moment.
Antworte NUR mit JSON in genau diesem Format:
{"score": <1-10, wie clipwürdig/viral ist der Moment>,
 "title": "<Titel, max 70 Zeichen, <SPRACHE>, neugierig machend, nicht irreführend, 1-2 Emojis>",
 "hook": "<Einblendung für die ersten Sekunden, max 5 Wörter, <SPRACHE>, ohne Emojis>",
 "hashtags": ["<3-5 passende Hashtags ohne #gta6>"],
 "reason": "<ein Satz, warum der Moment funktioniert oder nicht>"}"""


def llm_texts(cfg: dict, streamer: dict, category: str, transcript: str, chat_sample: list, stream_title: str) -> dict | None:
    llm = cfg["llm"]
    if not llm.get("enabled"):
        return None
    chat = "\n".join(f"{n}x {t}" for t, n in (chat_sample or [])[:12])
    user = (f"Streamer: {streamer.get('name')}\nStream-Titel: {stream_title or '-'}\nErkannte Stimmung: {category}\n\n"
            f"Transkript:\n{(transcript or '(kein Text)')[:1800]}\n\nChat in dem Moment:\n{chat or '(kein Chat)'}")
    try:
        resp = requests.post(llm["url"].rstrip("/") + "/api/chat", timeout=llm.get("timeout", 120), json={
            "model": llm["model"], "stream": False, "format": "json", "options": {"temperature": 0.7},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT.replace(
                "<SPRACHE>", "Englisch" if text_language(cfg) == "en" else "Deutsch")},
                {"role": "user", "content": user}],
        })
        resp.raise_for_status()
        data = json.loads(resp.json()["message"]["content"])
    except Exception as exc:  # noqa: BLE001
        log.warning("Ollama nicht erreichbar oder Antwort ungültig (%s) – nutze Vorlagen", exc)
        return None
    out = {}
    if isinstance(data.get("title"), str) and data["title"].strip():
        out["title"] = data["title"].strip()[:95]
    if isinstance(data.get("hook"), str) and data["hook"].strip():
        out["hook"] = data["hook"].strip()[:40]
    if isinstance(data.get("hashtags"), list):
        out["hashtags"] = [str(h) for h in data["hashtags"]][:5]
    try:
        out["score"] = max(1.0, min(10.0, float(data.get("score"))))
    except (TypeError, ValueError):
        pass
    if isinstance(data.get("reason"), str):
        out["reason"] = data["reason"][:300]
    return out


def make_texts(cfg: dict, streamer: dict, clip: dict) -> dict:
    category = clip.get("category") or "hype"
    base = template_texts(streamer, category, seed=int(clip.get("id") or 0), lang=text_language(cfg))
    llm = llm_texts(cfg, streamer, category, clip.get("transcript") or "", clip.get("chat_sample") or [],
                    clip.get("stream_title") or "") or {}
    title = llm.get("title") or base["title"]
    hook = llm.get("hook") or base["hook"]
    hashtags = build_hashtags(cfg, streamer, category, llm.get("hashtags"))
    return {
        "title": title, "hook": hook, "hashtags": hashtags,
        "caption": build_caption(title, hashtags, streamer),
        "llm_score": llm.get("score"), "llm_reason": llm.get("reason"),
    }
