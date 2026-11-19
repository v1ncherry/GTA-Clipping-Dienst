"""Lokales LLM über Ollama: automatisch erkennen, passendes Modell wählen, Modell per Klick laden.

Modus (llm.mode):
- auto : benutzen, wenn Ollama erreichbar ist und ein Modell installiert ist (Standard)
- on   : immer versuchen (Warnung, wenn nicht erreichbar)
- off  : nie
"""

from __future__ import annotations

import json
import logging
import threading
import time

import requests

log = logging.getLogger("clipper.llm")

RECOMMENDED = "qwen2.5:7b-instruct"          # gutes Deutsch, ~4,7 GB, läuft auf 8 GB VRAM oder CPU
PREFERRED = ["qwen2.5:7b-instruct", "qwen2.5:7b", "qwen2.5:14b-instruct", "qwen2.5:14b", "qwen3:8b", "qwen3:14b",
             "llama3.1:8b", "gemma3:12b", "gemma3:4b", "mistral-nemo", "mistral:7b", "llama3.2:3b", "qwen2.5:3b"]
SIZES = {"qwen2.5:7b-instruct": "4,7 GB", "qwen2.5:3b": "1,9 GB", "llama3.2:3b": "2,0 GB", "gemma3:4b": "3,3 GB"}

# Ollama läuft lokal oder im Heimnetz – nie über einen System-Proxy gehen
_http = requests.Session()
_http.trust_env = False

_lock = threading.Lock()
_cache: dict = {"t": 0.0, "url": None, "models": None}
_warned = {"t": 0.0}
_pull: dict = {"model": None, "status": None, "completed": 0, "total": 0, "error": None, "done": True}


def mode(cfg: dict) -> str:
    llm = cfg.get("llm") or {}
    m = str(llm.get("mode") or "").lower()
    if m in ("auto", "on", "off"):
        return m
    return "on" if llm.get("enabled") is True else "auto"   # alte config.yaml: "enabled: false" war nur Standard


def base_url(cfg: dict) -> str:
    return str((cfg.get("llm") or {}).get("url") or "http://localhost:11434").rstrip("/")


def list_models(cfg: dict, max_age: float = 30.0) -> list[str] | None:
    """Installierte Modelle (None = Ollama nicht erreichbar). Ergebnis wird kurz zwischengespeichert."""
    url = base_url(cfg)
    with _lock:
        if _cache["url"] == url and time.time() - _cache["t"] < max_age:
            return _cache["models"]
    try:
        data = _http.get(url + "/api/tags", timeout=2.5).json()
        models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except (requests.RequestException, ValueError):
        models = None
    with _lock:
        _cache.update(t=time.time(), url=url, models=models)
    return models


def _usable(name: str) -> bool:
    return not any(x in name.lower() for x in ("embed", "bge", "minilm", "nomic", "llava", "vision"))


def _match(wanted: str, installed: list[str]) -> str | None:
    wanted = wanted.strip().lower()
    for name in installed:
        n = name.lower()
        if n == wanted or n == wanted + ":latest" or (":" not in wanted and n.split(":")[0] == wanted):
            return name
    return None


def active_model(cfg: dict) -> str | None:
    """Welches Modell benutzt wird – oder None, wenn kein LLM verfügbar/gewünscht ist."""
    m = mode(cfg)
    if m == "off":
        return None
    models = list_models(cfg)
    if models is None:
        if m == "on" and time.time() - _warned["t"] > 300:
            _warned["t"] = time.time()
            log.warning("Ollama unter %s nicht erreichbar – Titel kommen aus Vorlagen", base_url(cfg))
        return None
    usable = [x for x in models if _usable(x)]
    wanted = str((cfg.get("llm") or {}).get("model") or "")
    if wanted and (hit := _match(wanted, usable)):
        return hit
    for pref in PREFERRED:
        if hit := _match(pref, usable):
            return hit
    return usable[0] if usable else None


def chat_json(cfg: dict, model: str, system: str, user: str) -> dict:
    payload = {"model": model, "stream": False, "format": "json", "options": {"temperature": 0.7},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if model.lower().startswith("qwen3"):
        payload["think"] = False                     # kein langes "Nachdenken", nur die Antwort
    resp = _http.post(base_url(cfg) + "/api/chat", json=payload,
                         timeout=float((cfg.get("llm") or {}).get("timeout", 120)))
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])


# ----------------------------------------------------------------------------- Modell laden

def pull_status() -> dict:
    with _lock:
        return dict(_pull)


def start_pull(cfg: dict, model: str = RECOMMENDED) -> dict:
    with _lock:
        if not _pull["done"]:
            return dict(_pull)
        _pull.update(model=model, status="Starte Download …", completed=0, total=0, error=None, done=False)
    threading.Thread(target=_pull_worker, args=(base_url(cfg), model), daemon=True, name="ollama-pull").start()
    return pull_status()


def _pull_worker(url: str, model: str) -> None:
    try:
        with _http.post(url + "/api/pull", json={"model": model, "stream": True}, stream=True,
                           timeout=(10, 600)) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise RuntimeError(msg["error"])
                with _lock:
                    _pull["status"] = msg.get("status") or _pull["status"]
                    if msg.get("total"):
                        _pull["total"], _pull["completed"] = msg["total"], msg.get("completed", 0)
        with _lock:
            _pull.update(status="Fertig", done=True)
            _cache["t"] = 0.0                         # Modellliste neu laden
        log.info("Ollama-Modell %s geladen", model)
    except Exception as exc:  # noqa: BLE001
        log.warning("Ollama-Modell %s konnte nicht geladen werden: %s", model, exc)
        with _lock:
            _pull.update(error=str(exc)[:300], status="Fehler", done=True)


def status(cfg: dict) -> dict:
    models = list_models(cfg, max_age=5)
    return {"mode": mode(cfg), "url": base_url(cfg), "reachable": models is not None,
            "models": [m for m in (models or []) if _usable(m)], "active": active_model(cfg),
            "recommended": RECOMMENDED, "recommended_size": SIZES.get(RECOMMENDED, ""), "pull": pull_status()}
