"""Twitch: Live-Check + Aufnahme über streamlink, Chat anonym über IRC-Websocket."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Iterator

from .base import ChatMessage, LiveInfo, Platform

log = logging.getLogger("clipper.twitch")

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"


def _parse_tags(line: str) -> tuple[dict[str, str], str]:
    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for part in raw_tags.split(";"):
            key, _, value = part.partition("=")
            tags[key] = value
    return tags, rest


def _sent_ts(tags: dict[str, str]) -> float:
    try:
        return int(tags.get("tmi-sent-ts", "0")) / 1000.0 or time.time()
    except ValueError:
        return time.time()


def parse_irc_line(line: str) -> ChatMessage | None:
    """Eine Twitch-IRC-Zeile (mit IRCv3-Tags) in eine ChatMessage umwandeln."""
    if " USERNOTICE #" in line:
        tags, _rest = _parse_tags(line)
        kind = {"raid": "raid", "submysterygift": "gifts"}.get(tags.get("msg-id", ""))
        if kind:
            text = tags.get("system-msg", "").replace("\\s", " ")
            return ChatMessage(ts=_sent_ts(tags), author=tags.get("login", "?"), text=text, kind=kind)
        return None
    if " PRIVMSG #" not in line:
        return None
    tags, rest = _parse_tags(line)
    prefix, _, rest = rest.partition(" PRIVMSG #")
    _channel, _, text = rest.partition(" :")
    author = tags.get("display-name") or prefix.lstrip(":").split("!")[0]
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    return ChatMessage(ts=_sent_ts(tags), author=author, text=text.strip(), color=tags.get("color") or None)


class Twitch(Platform):
    name = "twitch"

    def streamlink_args(self) -> list[str]:
        return ["--twitch-low-latency", "--twitch-supported-codecs", "h264"]

    def chat_messages(self, info: LiveInfo, stop: threading.Event) -> Iterator[ChatMessage]:
        from websockets.sync.client import connect

        backoff = 2.0
        while not stop.is_set():
            try:
                with connect(IRC_URL, open_timeout=15, close_timeout=3, max_size=2**22) as ws:
                    ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                    ws.send("PASS SCHMOOPIIE")
                    ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                    ws.send(f"JOIN #{self.channel.lower()}")
                    log.info("Twitch-Chat verbunden: #%s", self.channel)
                    backoff = 2.0
                    while not stop.is_set():
                        try:
                            data = ws.recv(timeout=1.0)
                        except TimeoutError:
                            continue
                        if isinstance(data, bytes):
                            data = data.decode("utf-8", "replace")
                        for line in data.split("\r\n"):
                            if not line:
                                continue
                            if line.startswith("PING"):
                                ws.send("PONG :tmi.twitch.tv")
                                continue
                            if " RECONNECT" in line:
                                raise ConnectionError("Twitch fordert Reconnect")
                            msg = parse_irc_line(line)
                            if msg:
                                yield msg
            except Exception as exc:  # noqa: BLE001
                if stop.is_set():
                    break
                log.warning("Twitch-Chat getrennt (%s), neuer Versuch in %.0fs", exc, backoff)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)
