#!/usr/bin/env python3
"""Play the most recently listened Audible book via Music Assistant.

This script finds the most recently listened audiobook from your Audible library
and starts it playing in Music Assistant, resuming from the Audible position.

Usage:
    python3 play_recent_audible.py

Prerequisites:
    pip install audible aiohttp
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

import aiohttp
import audible

# ── Configuration (edit these) ─────────────────────────────────────────────────

# Path to your Audible auth file (in MA storage: /config/.musicassistant/ on HA).
# Filename is audible_auth_<uuid>.json
AUDIBLE_AUTH_FILE = "/path/to/audible_auth_XXXX.json"

# MA server URL (usually http://homeassistant.local:8095 or http://localhost:8095)
MA_URL = "http://homeassistant.local:8095"

# MA player queue ID — the speaker to play on.
# Find it in MA UI → Players → click a player → copy the ID from the URL.
MA_QUEUE_ID = "your_player_queue_id"

# MA credentials (create a user in MA UI → Settings → Users).
# Alternatively, set MA_TOKEN below to skip login on each run.
MA_USERNAME = "admin"
MA_PASSWORD = "your_password"

# Optional: paste a long-lived MA access token here to skip username/password login.
# Leave as empty string to use MA_USERNAME/MA_PASSWORD instead.
MA_TOKEN = ""

# ───────────────────────────────────────────────────────────────────────────────


async def get_most_recent_asin() -> str:
    """Return the ASIN of the most recently listened Audible audiobook."""
    auth = await asyncio.to_thread(audible.Authenticator.from_file, AUDIBLE_AUTH_FILE)
    async with audible.AsyncClient(auth) as client:
        response = await client.get(
            "library",
            response_groups="product_attrs",
            sort_by="-RecentlyListened",
            num_results=1,
        )
    items = response.get("items", [])
    if not items:
        print("No items found in Audible library.", file=sys.stderr)
        sys.exit(1)
    asin = items[0].get("asin")
    title = items[0].get("title", "Unknown")
    if not asin:
        print("Could not read ASIN from Audible response.", file=sys.stderr)
        sys.exit(1)
    print(f"Most recently listened: {title!r} (ASIN: {asin})")
    return asin


async def get_ma_token(session: aiohttp.ClientSession) -> str:
    """Log in to MA and return an access token."""
    if MA_TOKEN:
        return MA_TOKEN
    url = f"{MA_URL}/auth/login"
    payload = {
        "provider_id": "builtin",
        "credentials": {"username": MA_USERNAME, "password": MA_PASSWORD},
        "device_name": "play_recent_audible_script",
    }
    async with session.post(url, json=payload) as resp:
        if resp.status != 200:
            text = await resp.text()
            print(f"MA login failed ({resp.status}): {text}", file=sys.stderr)
            sys.exit(1)
        data = await resp.json()
    if not data.get("success"):
        print(f"MA login rejected: {data.get('error')}", file=sys.stderr)
        sys.exit(1)
    return data["token"]


async def play_in_ma(asin: str) -> None:
    """Connect to MA via WebSocket and trigger playback of the given ASIN."""
    # MA URI format for an Audible audiobook
    media_uri = f"audible://audiobook/{asin}"

    ws_url = MA_URL.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    async with aiohttp.ClientSession() as session:
        token = await get_ma_token(session)

        async with session.ws_connect(ws_url) as ws:
            # 1. Receive server info (first message MA sends on connect)
            msg = await ws.receive_json()
            if not msg:
                print("No server info received from MA.", file=sys.stderr)
                sys.exit(1)

            # 2. Authenticate
            auth_id = str(uuid.uuid4())
            await ws.send_str(
                json.dumps(
                    {"message_id": auth_id, "command": "auth", "args": {"token": token}}
                )
            )
            auth_response = await ws.receive_json()
            if "error_code" in auth_response:
                print(f"MA auth failed: {auth_response.get('details')}", file=sys.stderr)
                sys.exit(1)

            # 3. Send play_media command
            play_id = str(uuid.uuid4())
            await ws.send_str(
                json.dumps(
                    {
                        "message_id": play_id,
                        "command": "player_queues/play_media",
                        "args": {
                            "queue_id": MA_QUEUE_ID,
                            "media": media_uri,
                        },
                    }
                )
            )

            # 4. Wait for response
            play_response = await ws.receive_json()
            if "error_code" in play_response:
                print(f"MA play_media failed: {play_response.get('details')}", file=sys.stderr)
                sys.exit(1)

            print(f"Playback started: {media_uri}")


async def main() -> None:
    """Find the most recent Audible book and play it in MA."""
    asin = await get_most_recent_asin()
    await play_in_ma(asin)


if __name__ == "__main__":
    asyncio.run(main())
