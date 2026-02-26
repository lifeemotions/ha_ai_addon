"""Home Assistant API client for interacting with the HA WebSocket and REST APIs."""

import json
import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger("lifeemotions_ai_addon")

HA_WS_URL = "ws://supervisor/core/websocket"


async def check_recorder_dialect() -> Optional[str]:
    """
    Query the Home Assistant WebSocket API to determine the Recorder's database dialect.

    Returns the dialect string ("sqlite", "mysql", "postgresql") on success,
    or None if the check could not be performed.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.warning("SUPERVISOR_TOKEN not available, cannot verify Recorder database type")
        return None

    try:
        session = aiohttp.ClientSession()
        try:
            async with session.ws_connect(HA_WS_URL) as ws:
                # Step 1: Receive auth_required message
                msg = await ws.receive_json()
                if msg.get("type") != "auth_required":
                    logger.warning(f"Unexpected WebSocket message: {msg.get('type')}")
                    return None

                # Step 2: Authenticate
                await ws.send_json({"type": "auth", "access_token": token})
                msg = await ws.receive_json()
                if msg.get("type") != "auth_ok":
                    logger.warning(f"WebSocket authentication failed: {msg.get('message', 'unknown error')}")
                    return None

                # Step 3: Request recorder info
                await ws.send_json({"id": 1, "type": "recorder/info"})
                msg = await ws.receive_json()

                if not msg.get("success"):
                    logger.warning(f"recorder/info request failed: {msg.get('error', {}).get('message', 'unknown')}")
                    return None

                # Step 4: Extract dialect
                result = msg.get("result", {})
                engine = result.get("database_engine", {})
                dialect = engine.get("dialect")

                if dialect:
                    logger.info(f"Recorder database dialect: {dialect}")
                    return dialect
                else:
                    logger.warning("recorder/info response did not include database dialect")
                    return None

        finally:
            await session.close()

    except (aiohttp.ClientError, json.JSONDecodeError, ConnectionError, OSError) as e:
        logger.warning(f"Could not connect to Home Assistant WebSocket API: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error checking Recorder dialect: {e}")
        return None
