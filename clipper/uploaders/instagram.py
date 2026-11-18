"""Instagram Reels Upload über die Instagram API mit Instagram-Login (Business- oder Creator-Account nötig).

Upload läuft "resumable" direkt vom eigenen Rechner – es wird keine öffentliche Video-URL gebraucht.
Solange die Meta-App im Entwicklungsmodus ist, funktioniert das für Accounts, die als Tester eingetragen sind
(für eure eigenen Clip-Accounts reicht das).
"""

from __future__ import annotations

import os
import time

import requests


def _base(cfg: dict) -> tuple[str, str]:
    ig = cfg["uploads"]["instagram"]
    return ig.get("api_base", "https://graph.instagram.com").rstrip("/"), ig.get("api_version", "v21.0")


def connect(cfg: dict, token: str) -> tuple[str, dict]:
    base, ver = _base(cfg)
    resp = requests.get(f"{base}/{ver}/me", params={"fields": "id,user_id,username", "access_token": token},
                        timeout=30).json()
    if "error" in resp:
        raise RuntimeError(f"Instagram: {resp['error'].get('message')}")
    return "@" + resp.get("username", "instagram"), {
        "access_token": token, "user_id": str(resp.get("user_id") or resp.get("id")),
        "username": resp.get("username"), "obtained_at": time.time(),
    }


def _token(cfg: dict, account: dict, db) -> str:
    c = account["credentials"]
    if time.time() - c.get("obtained_at", 0) > 7 * 86400:   # Long-Lived-Token (60 Tage) regelmäßig verlängern
        base, _ver = _base(cfg)
        resp = requests.get(f"{base}/refresh_access_token", timeout=30,
                            params={"grant_type": "ig_refresh_token", "access_token": c["access_token"]}).json()
        if resp.get("access_token"):
            c = {**c, "access_token": resp["access_token"], "obtained_at": time.time()}
            db.update_account(account["id"], credentials=c)
    return c["access_token"]


def _check(resp: requests.Response) -> dict:
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"Instagram: {body['error'].get('message')}")
    return body


def upload(cfg: dict, account: dict, db, path: str, caption: str) -> str:
    base, ver = _base(cfg)
    token = _token(cfg, account, db)
    uid = account["credentials"]["user_id"]
    container = _check(requests.post(f"{base}/{ver}/{uid}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
        "share_to_feed": "true" if cfg["uploads"]["instagram"].get("share_to_feed", True) else "false",
        "access_token": token}))
    cid = container["id"]
    upload_uri = container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{ver}/{cid}"
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        r = requests.post(upload_uri, data=fh, timeout=900, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    if r.status_code >= 300:
        raise RuntimeError(f"Instagram-Upload: HTTP {r.status_code} {r.text[:200]}")
    for _ in range(120):
        st = _check(requests.get(f"{base}/{ver}/{cid}", timeout=30,
                                 params={"fields": "status_code,status", "access_token": token}))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram-Verarbeitung fehlgeschlagen: {st.get('status')}")
        time.sleep(5)
    else:
        raise RuntimeError("Instagram: Verarbeitung dauert zu lange")
    media = _check(requests.post(f"{base}/{ver}/{uid}/media_publish", timeout=60,
                                 data={"creation_id": cid, "access_token": token}))
    link = requests.get(f"{base}/{ver}/{media['id']}", timeout=30,
                        params={"fields": "permalink", "access_token": token}).json()
    return link.get("permalink") or f"Instagram: veröffentlicht ({media['id']})"
