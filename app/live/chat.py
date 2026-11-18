"""Live chat listeners. Every message becomes (wall-clock time, user, text).

- Twitch: anonymous read-only IRC over websocket (the "justinfan" login needs no account).
- Kick: the public Pusher websocket the Kick website itself uses (unofficial, may change).
- YouTube: not yet; YouTube streams rely on audio and transcript signals.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections import deque
from typing import Callable

import websockets

KICK_PUSHER = "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679?protocol=7&client=js&version=8.4.0&flash=false"
_PRIV = re.compile(r"^(?:@\S+ )?:(\w+)!\S+ PRIVMSG #\w+ :(.*)$")


def channel_of(url: str) -> tuple[str, str] | None:
    m = re.search(r"(twitch\.tv|kick\.com)/([A-Za-z0-9_]+)", url)
    if not m:
        return None
    return ("twitch" if "twitch" in m.group(1) else "kick"), m.group(2).lower()


async def _twitch(name: str, emit: Callable) -> None:
    async with websockets.connect("wss://irc-ws.chat.twitch.tv:443", ping_interval=60) as ws:
        await ws.send("PASS SCHMOOPIIE")
        await ws.send("NICK justinfan%d" % (int(time.time()) % 90000 + 10000))
        await ws.send(f"JOIN #{name}")
        async for raw in ws:
            for line in str(raw).split("\r\n"):
                if line.startswith("PING"):
                    await ws.send("PONG :tmi.twitch.tv")
                elif m := _PRIV.match(line):
                    emit(time.time(), m.group(1), m.group(2))


def _kick_chatroom(name: str) -> int:
    from curl_cffi import requests  # Kick's API rejects clients without a browser-like handshake
    r = requests.get(f"https://kick.com/api/v2/channels/{name}", impersonate="chrome", timeout=20)
    r.raise_for_status()
    return int(r.json()["chatroom"]["id"])


async def _kick(name: str, emit: Callable) -> None:
    room = await asyncio.to_thread(_kick_chatroom, name)
    async with websockets.connect(KICK_PUSHER, ping_interval=60) as ws:
        await ws.send(json.dumps({"event": "pusher:subscribe",
                                  "data": {"auth": "", "channel": f"chatrooms.{room}.v2"}}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("event") == "pusher:ping":
                await ws.send(json.dumps({"event": "pusher:pong", "data": {}}))
            elif msg.get("event", "").endswith("ChatMessageEvent"):
                data = json.loads(msg["data"]) if isinstance(msg["data"], str) else msg["data"]
                emit(time.time(), (data.get("sender") or {}).get("username", "?"), data.get("content", ""))


class ChatListener:
    """Runs a chat connection in a background thread and keeps the last hour of messages."""

    def __init__(self, url: str):
        self.target = channel_of(url)
        self.messages: deque[tuple[float, str, str]] = deque(maxlen=200_000)
        self.connected = False
        self.error = ""
        self._stop = False

    def _emit(self, t: float, user: str, text: str) -> None:
        self.messages.append((t, user, text))

    async def _run(self) -> None:
        delay = 2
        while not self._stop:
            try:
                self.connected = True
                kind, name = self.target
                await (_twitch(name, self._emit) if kind == "twitch" else _kick(name, self._emit))
            except Exception as exc:  # noqa: BLE001 - reconnect on any network failure
                self.error = str(exc)[:200]
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)    # capped: a long blind spot loses clips

    def start(self) -> bool:
        if not self.target:
            return False
        threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop = True

    def between(self, t0: float, t1: float) -> list[tuple[float, str, str]]:
        return [m for m in list(self.messages) if t0 <= m[0] < t1]
