"""Kick: Live-Check über die Kick-API (mit Browser-Fingerprint gegen Cloudflare), Chat über Pusher-Websocket."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Iterator

from .base import ChatMessage, LiveInfo, Platform, streamlink_check

log = logging.getLogger("clipper.kick")

API_CHANNEL = "https://kick.com/api/v2/channels/{slug}"
PUSHER_URL = "wss://ws-us2.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0&flash=false"
EMOTE_RE = re.compile(r"\[emote:\d+:([^\]]+)\]")


def _http_get_json(url: str) -> dict:
    try:
        from curl_cffi import requests as creq

        resp = creq.get(url, impersonate="chrome", timeout=20)
    except ImportError:
        import requests

        resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def parse_pusher_message(raw: str) -> ChatMessage | None:
    try:
        evt = json.loads(raw)
    except ValueError:
        return None
    if evt.get("event") != "App\\Events\\ChatMessageEvent":
        return None
    data = evt.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    text = EMOTE_RE.sub(r"\1", data.get("content") or "").strip()
    author = (data.get("sender") or {}).get("username") or "?"
    ts = time.time()
    created = data.get("created_at")
    if created:
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return ChatMessage(ts=ts, author=author, text=text) if text else None


class Kick(Platform):
    name = "kick"

    def streamlink_args(self) -> list[str]:
        return ["--kick-low-latency"]

    def check_live(self) -> LiveInfo:
        try:
            data = _http_get_json(API_CHANNEL.format(slug=self.channel))
            stream = data.get("livestream")
            chatroom = (data.get("chatroom") or {}).get("id")
            if stream:
                cats = stream.get("categories") or []
                category = cats[0].get("name") if cats else None
                return LiveInfo(True, title=stream.get("session_title"), category=category,
                                video_id=str(stream.get("id")), extra={"chatroom_id": chatroom})
            return LiveInfo(False, extra={"chatroom_id": chatroom})
        except Exception as exc:  # noqa: BLE001
            log.debug("Kick-API fehlgeschlagen (%s), nutze streamlink", exc)
            return streamlink_check(self.url)

    def chat_messages(self, info: LiveInfo, stop: threading.Event) -> Iterator[ChatMessage]:
        from websockets.sync.client import connect

        chatroom = info.extra.get("chatroom_id")
        if not chatroom:
            try:
                chatroom = (_http_get_json(API_CHANNEL.format(slug=self.channel)).get("chatroom") or {}).get("id")
            except Exception as exc:  # noqa: BLE001
                log.warning("Kick: Chatroom-ID nicht ermittelbar (%s) – Chat wird ignoriert", exc)
                return
        key = self.cfg["monitor"].get("kick_pusher_key") or "32cbd69e4b950bf97679"
        backoff = 2.0
        while not stop.is_set():
            try:
                with connect(PUSHER_URL.format(key=key), open_timeout=15, close_timeout=3) as ws:
                    for channel in (f"chatrooms.{chatroom}.v2", f"chatroom_{chatroom}"):
                        ws.send(json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": channel}}))
                    log.info("Kick-Chat verbunden: %s (Chatroom %s)", self.channel, chatroom)
                    backoff = 2.0
                    while not stop.is_set():
                        try:
                            raw = ws.recv(timeout=1.0)
                        except TimeoutError:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        if '"pusher:ping"' in raw:
                            ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
                            continue
                        msg = parse_pusher_message(raw)
                        if msg:
                            yield msg
            except Exception as exc:  # noqa: BLE001
                if stop.is_set():
                    break
                log.warning("Kick-Chat getrennt (%s), neuer Versuch in %.0fs", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)
