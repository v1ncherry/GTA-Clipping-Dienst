"""TikTok Upload über die Content Posting API.

Modi:
- inbox  : Video landet als Entwurf in der TikTok-App (Benachrichtigung), dort mit Sound/Text fertig posten.
           Funktioniert auch, bevor die eigene TikTok-App das Audit bestanden hat.
- direct : wird direkt veröffentlicht. Ohne TikTok-Audit nur als "privat" (SELF_ONLY) möglich.
"""

from __future__ import annotations

import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.upload,video.publish"


def _conf(cfg: dict) -> dict:
    t = cfg["uploads"]["tiktok"]
    if not t.get("client_key") or not t.get("client_secret") or not t.get("redirect_uri"):
        raise RuntimeError("TikTok: client_key, client_secret und redirect_uri in config.yaml eintragen (siehe README)")
    return t


def auth_url(cfg: dict) -> str:
    t = _conf(cfg)
    return AUTH_URL + "?" + urlencode({"client_key": t["client_key"], "scope": SCOPES, "response_type": "code",
                                       "redirect_uri": t["redirect_uri"], "state": secrets.token_urlsafe(12)})


def _token_request(cfg: dict, data: dict) -> dict:
    t = _conf(cfg)
    resp = requests.post(TOKEN_URL, timeout=30, headers={"Content-Type": "application/x-www-form-urlencoded"},
                         data={"client_key": t["client_key"], "client_secret": t["client_secret"], **data})
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(f"TikTok-Token-Fehler: {body}")
    body["obtained_at"] = time.time()
    return body


def connect(cfg: dict, redirected_url: str) -> tuple[str, dict]:
    code = parse_qs(urlparse(redirected_url.strip()).query).get("code", [redirected_url.strip()])[0]
    tokens = _token_request(cfg, {"code": code, "grant_type": "authorization_code",
                                  "redirect_uri": _conf(cfg)["redirect_uri"]})
    info = requests.get(f"{API}/user/info/", params={"fields": "open_id,display_name,username"}, timeout=30,
                        headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    user = (info.get("data") or {}).get("user") or {}
    tokens["username"] = user.get("username") or ""
    return user.get("display_name") or user.get("username") or "TikTok", tokens


def _access_token(cfg: dict, account: dict, db) -> str:
    c = account["credentials"]
    if time.time() > c.get("obtained_at", 0) + c.get("expires_in", 0) - 300:
        new = _token_request(cfg, {"grant_type": "refresh_token", "refresh_token": c["refresh_token"]})
        new["username"] = c.get("username", "")
        db.update_account(account["id"], credentials=new)
        c = new
    return c["access_token"]


def _post(token: str, path: str, payload: dict) -> dict:
    resp = requests.post(f"{API}{path}", json=payload, timeout=60,
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
    body = resp.json()
    err = body.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok {path}: {err.get('code')} – {err.get('message')}")
    return body.get("data") or {}


def upload(cfg: dict, account: dict, db, path: str, caption: str) -> str:
    t = _conf(cfg)
    token = _access_token(cfg, account, db)
    size = os.path.getsize(path)
    chunk = size if size <= 64 * 1024 * 1024 else 10 * 1024 * 1024
    total = max(1, size // chunk)
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": total}
    mode = t.get("mode", "inbox")
    if mode == "direct":
        options = _post(token, "/post/publish/creator_info/query/", {}).get("privacy_level_options") or []
        privacy = t.get("privacy", "PUBLIC_TO_EVERYONE")
        if options and privacy not in options:
            privacy = options[0]
        data = _post(token, "/post/publish/video/init/", {
            "post_info": {"title": caption[:2200], "privacy_level": privacy, "disable_duet": False,
                          "disable_comment": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000},
            "source_info": source})
    else:
        data = _post(token, "/post/publish/inbox/video/init/", {"source_info": source})
    upload_url, publish_id = data["upload_url"], data["publish_id"]
    with open(path, "rb") as fh:
        for i in range(total):
            first = i * chunk
            last = size - 1 if i == total - 1 else first + chunk - 1
            fh.seek(first)
            part = fh.read(last - first + 1)
            r = requests.put(upload_url, data=part, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                "Content-Range": f"bytes {first}-{last}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"TikTok-Upload Chunk {i + 1}/{total}: HTTP {r.status_code} {r.text[:200]}")
    for _ in range(120):
        st = _post(token, "/post/publish/status/fetch/", {"publish_id": publish_id})
        status = st.get("status")
        if status == "FAILED":
            raise RuntimeError(f"TikTok: Veröffentlichung fehlgeschlagen ({st.get('fail_reason')})")
        if status == "SEND_TO_USER_INBOX":
            return "TikTok-Inbox (Entwurf in der App fertig posten)"
        if status == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or []
            user = account["credentials"].get("username")
            if ids and user:
                return f"https://www.tiktok.com/@{user}/video/{ids[0]}"
            return "TikTok: veröffentlicht"
        time.sleep(5)
    return "TikTok: wird noch verarbeitet"
