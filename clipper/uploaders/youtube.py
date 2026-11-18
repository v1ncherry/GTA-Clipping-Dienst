"""YouTube Shorts Upload über die YouTube Data API v3 (OAuth, kostenloses Kontingent).

Hinweis: Uploads aus nicht verifizierten Google-Cloud-Projekten sind auf "privat" beschränkt, bis das
Projekt das YouTube-API-Audit bestanden hat. Upload kostet 1.600 von 10.000 Kontingent-Einheiten pro Tag
(= ca. 6 Uploads/Tag pro Projekt, Erhöhung kann beantragt werden).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def _secrets_path(cfg: dict) -> str:
    p = Path(cfg["uploads"]["youtube"]["client_secrets"])
    if not p.is_absolute():
        p = Path(cfg.get("_config_path", "config.yaml")).resolve().parent / p
    if not p.exists():
        raise FileNotFoundError(f"client_secret.json nicht gefunden: {p} (siehe README: YouTube einrichten)")
    return str(p)


def _service(creds):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def connect(cfg: dict, manual: bool = False) -> tuple[str, dict]:
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(_secrets_path(cfg), SCOPES)
    if manual:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        flow.redirect_uri = "http://localhost:8765/"
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        print("\n1) Diesen Link im Browser öffnen und den Kanal auswählen:\n\n" + url)
        print("\n2) Danach landet der Browser auf einer Fehlerseite (localhost). Die komplette URL aus der "
              "Adresszeile hier einfügen:")
        flow.fetch_token(authorization_response=input("> ").strip())
        creds = flow.credentials
    else:
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    items = _service(creds).channels().list(part="snippet", mine=True).execute().get("items", [])
    name = items[0]["snippet"]["title"] if items else "YouTube"
    return name, json.loads(creds.to_json())


def upload(cfg: dict, account: dict, db, path: str, title: str, description: str, tags: list[str]) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.http import MediaFileUpload

    creds = Credentials.from_authorized_user_info(account["credentials"], SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        db.update_account(account["id"], credentials=json.loads(creds.to_json()))
    yt = cfg["uploads"]["youtube"]
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                    "categoryId": str(yt.get("category_id", "20"))},
        "status": {"privacyStatus": yt.get("privacy", "public"), "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = _service(creds).videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _status, resp = req.next_chunk()
    return f"https://youtube.com/shorts/{resp['id']}"
