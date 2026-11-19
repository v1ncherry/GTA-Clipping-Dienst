"""Aus Feedback lernen: Hochgeladene Clips zählen als gut, verworfene mit Grund als schlecht.

Pro Streamer werden daraus behutsam zwei Stellschrauben angepasst (immer nur ein kleiner Schritt, danach wird
neu gezählt):
- viel "Nur Chat-Spam" verworfen  -> mehr Gewicht auf die Reaktion im Stream + etwas höhere Schwelle
- viel "Langweilig" verworfen      -> höhere Schwelle (weniger, dafür stärkere Clips)
- fast alles wird hochgeladen      -> etwas niedrigere Schwelle (mehr Clips)
"""

from __future__ import annotations

import logging
import time

from .config import deep_merge, effective_config
from .db import DB

log = logging.getLogger("clipper.learn")

REASONS = {"spam": "Nur Chat-Spam", "boring": "Langweilig", "cut": "Falscher Ausschnitt", "other": "Anderes"}
LIMITS = {"stream_share": (20, 65), "trigger_score": (62, 88)}


def record(cfg: dict, db: DB, clip: dict, verdict: str, reason: str | None = None) -> str | None:
    """Feedback speichern und ggf. den Streamer nachjustieren. Gibt eine Notiz zurück, wenn angepasst wurde."""
    if verdict == "good" and db.one("SELECT id FROM feedback WHERE clip_id = ? AND verdict = 'good'", (clip["id"],)):
        return None
    db.add_feedback(clip["id"], clip["streamer_id"], verdict, reason if reason in REASONS else None)
    if not effective_config(cfg, db).get("learn", {}).get("enabled", True):
        return None
    return adjust(cfg, db, clip["streamer_id"])


def adjust(cfg: dict, db: DB, sid: int) -> str | None:
    streamer = db.get_streamer(sid)
    if not streamer:
        return None
    key = f"learn:{sid}"
    state = db.get_setting(key) or {"since": 0, "log": []}
    rows = db.query("SELECT verdict, reason FROM feedback WHERE streamer_id = ? AND created_at > ? "
                    "ORDER BY id DESC LIMIT 20", (sid, state.get("since", 0)))
    spam = sum(r["reason"] == "spam" for r in rows)
    boring = sum(r["reason"] == "boring" for r in rows)
    good = sum(r["verdict"] == "good" for r in rows)
    bad = sum(r["verdict"] == "bad" for r in rows)
    det = effective_config(cfg, db, streamer)["detection"]
    share, trig = float(det.get("stream_share", 35)), float(det["trigger_score"])
    changes, note = {}, None
    if spam >= 3:
        changes = {"stream_share": share + 5, "trigger_score": trig + 1}
        note = "Mehr Gewicht auf die Reaktion im Stream, weil Chat-Spam verworfen wurde"
    elif boring >= 3:
        changes = {"trigger_score": trig + 2}
        note = "Höhere Schwelle, weil langweilige Clips verworfen wurden"
    elif good >= 8 and bad <= 1:
        changes = {"trigger_score": trig - 1}
        note = "Etwas niedrigere Schwelle, weil die Clips gut ankommen"
    if not changes:
        return None
    changes = {k: int(min(max(v, LIMITS[k][0]), LIMITS[k][1])) for k, v in changes.items()}
    if all(changes[k] == int(det.get(k, -1)) for k in changes):
        return None                                 # schon am Anschlag
    settings = deep_merge(streamer.get("settings") or {}, {"detection": changes})
    db.update_streamer(sid, settings=settings)
    state["since"] = time.time()
    state["log"] = ([{"time": time.time(), "note": note, "changes": changes}] + state.get("log", []))[:10]
    db.set_setting(key, state)
    log.info("%s automatisch angepasst: %s (%s)", streamer["name"], note, changes)
    return note


def last_note(db: DB, sid: int) -> dict | None:
    state = db.get_setting(f"learn:{sid}") or {}
    entries = state.get("log") or []
    return entries[0] if entries else None
