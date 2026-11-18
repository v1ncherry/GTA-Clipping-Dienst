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


def credit_line(streamer: dict) -> str:
    url = re.sub(r"^https?://(www\.)?", "", streamer.get("url", "")).rstrip("/")
    return url if streamer.get("platform") != "local" else streamer.get("name", "")


def build_hashtags(cfg: dict, streamer: dict, category: str, extra: list[str] | None = None) -> list[str]:
    tags = list(cfg.get("hashtags", []))
    tags.append("#" + slugify(streamer.get("name", ""), 30).replace("-", ""))
    tags += CATEGORY_TAGS.get(category, [])
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


def template_texts(streamer: dict, category: str, seed: int) -> dict:
    rng = random.Random(seed)
    cat = category if category in TITLES else "hype"
    name = streamer.get("name", "Er")
    return {"title": rng.choice(TITLES[cat]).format(n=name), "hook": rng.choice(HOOKS[cat])}


SYSTEM_PROMPT = """Du bist Social-Media-Editor für virale GTA-6-Shorts (TikTok, YouTube Shorts, Instagram Reels).
Du bekommst das Transkript eines Stream-Ausschnitts und die häufigsten Chat-Nachrichten in dem Moment.
Antworte NUR mit JSON in genau diesem Format:
{"score": <1-10, wie clipwürdig/viral ist der Moment>,
 "title": "<Titel, max 70 Zeichen, Deutsch, neugierig machend, nicht irreführend, 1-2 Emojis>",
 "hook": "<Einblendung für die ersten Sekunden, max 5 Wörter, ohne Emojis>",
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
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
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
    base = template_texts(streamer, category, seed=int(clip.get("id") or 0))
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
