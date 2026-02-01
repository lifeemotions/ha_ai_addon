#!/usr/bin/env python3
"""
Life Emotions HA Plugin

Extracts historical device events from the local SQLite database
and streams them to a remote Cloud API.
"""

import asyncio
import json
import logging
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from const import (
    API_ENDPOINT,
    BATCH_SIZE,
    CHECKPOINT_FILE,
    CLOUD_AUTH_TOKEN,
    DATABASE_PATH,
    DEFAULT_API_ENDPOINT,
    MAX_RETRIES,
    ORIGIN,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_DELAY_SECONDS,
    SYNC_INTERVAL_MINUTES,
)

# Configure logging for Home Assistant Supervisor log stream
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("lifeemotions_ha_plugin")


class CheckpointManager:
    """Manages checkpoint state for tracking processed events."""

    def __init__(self, checkpoint_file: str = CHECKPOINT_FILE):
        self.checkpoint_file = Path(checkpoint_file)
        self._ensure_data_dir()

    def _ensure_data_dir(self) -> None:
        """Ensure the data directory exists."""
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, int]:
        """Load checkpoint from file."""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, "r") as f:
                    data = json.load(f)
                    logger.info(f"Loaded checkpoint: {data}")
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load checkpoint: {e}. Starting fresh.")
        return {"last_event_id": 0, "last_state_id": 0}

    def save(self, last_event_id: int, last_state_id: int) -> None:
        """Save checkpoint to file."""
        data = {"last_event_id": last_event_id, "last_state_id": last_state_id}
        try:
            with open(self.checkpoint_file, "w") as f:
                json.dump(data, f)
            logger.debug(f"Saved checkpoint: {data}")
        except IOError as e:
            logger.error(f"Failed to save checkpoint: {e}")


class DatabaseReader:
    """Reads events and states from the Home Assistant SQLite database."""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._has_event_types_table: Optional[bool] = None

    def _get_connection(self) -> sqlite3.Connection:
        """Create a read-only database connection."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _check_event_types_table(self, conn: sqlite3.Connection) -> bool:
        """Check if the event_types table exists (HA 2023.4+ schema)."""
        if self._has_event_types_table is None:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='event_types'"
            )
            self._has_event_types_table = cursor.fetchone() is not None
            if self._has_event_types_table:
                logger.info("Detected HA 2023.4+ schema (event_types table present)")
            else:
                logger.info("Using HA 2022.6+ schema (event_type column on events table)")
        return self._has_event_types_table

    def fetch_events(self, last_event_id: int, batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
        """
        Fetch events from the database starting after last_event_id.

        Supports both HA 2022.6+ schema (event_type on events table) and
        HA 2023.4+ schema (event_type in separate event_types table).
        """
        events = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                if self._check_event_types_table(conn):
                    # HA 2023.4+ schema: event_type in separate table
                    query = """
                        SELECT
                            e.event_id,
                            et.event_type,
                            e.time_fired_ts,
                            e.origin_idx,
                            ed.shared_data as event_data
                        FROM events e
                        LEFT JOIN event_types et ON e.event_type_id = et.event_type_id
                        LEFT JOIN event_data ed ON e.data_id = ed.data_id
                        WHERE e.event_id > ?
                        ORDER BY e.event_id ASC
                        LIMIT ?
                    """
                else:
                    # HA 2022.6+ schema: event_type directly on events table
                    query = """
                        SELECT
                            e.event_id,
                            e.event_type,
                            e.time_fired_ts,
                            e.origin_idx,
                            ed.shared_data as event_data
                        FROM events e
                        LEFT JOIN event_data ed ON e.data_id = ed.data_id
                        WHERE e.event_id > ?
                        ORDER BY e.event_id ASC
                        LIMIT ?
                    """

                cursor.execute(query, (last_event_id, batch_size))
                rows = cursor.fetchall()

                for row in rows:
                    event = self._parse_event_row(row)
                    if event:
                        events.append(event)

                logger.info(f"Fetched {len(events)} events from database")

        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                logger.warning(f"Database busy/locked fetching events (will retry next cycle): {e}")
            else:
                logger.error(f"Database error fetching events: {e}")
        except sqlite3.Error as e:
            logger.error(f"Database error fetching events: {e}")

        return events

    def fetch_states(self, last_state_id: int, batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
        """
        Fetch states from the database starting after last_state_id.

        Uses the newer HA database schema where attributes are in a separate table.
        """
        states = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Query states with joined state_attributes and states_meta (HA 2022.6+ schema)
                query = """
                    SELECT
                        s.state_id,
                        sm.entity_id,
                        s.state,
                        s.last_updated_ts,
                        s.last_changed_ts,
                        sa.shared_attrs as attributes
                    FROM states s
                    LEFT JOIN states_meta sm ON s.metadata_id = sm.metadata_id
                    LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
                    WHERE s.state_id > ?
                    ORDER BY s.state_id ASC
                    LIMIT ?
                """
                cursor.execute(query, (last_state_id, batch_size))
                rows = cursor.fetchall()

                for row in rows:
                    state = self._parse_state_row(row)
                    if state:
                        states.append(state)

                logger.info(f"Fetched {len(states)} states from database")

        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                logger.warning(f"Database busy/locked fetching states (will retry next cycle): {e}")
            else:
                logger.error(f"Database error fetching states: {e}")
        except sqlite3.Error as e:
            logger.error(f"Database error fetching states: {e}")

        return states

    def _parse_event_row(self, row: sqlite3.Row) -> Optional[dict[str, Any]]:
        """Parse a database row into an event dictionary."""
        try:
            # Parse timestamp from Unix timestamp
            timestamp = datetime.fromtimestamp(row["time_fired_ts"], tz=timezone.utc).isoformat()
            
            # Parse event data JSON
            attributes = {}
            if row["event_data"]:
                try:
                    attributes = json.loads(row["event_data"])
                except json.JSONDecodeError:
                    pass
            
            return {
                "id": row["event_id"],
                "type": "event",
                "timestamp": timestamp,
                "entity_id": attributes.get("entity_id", ""),
                "event_type": row["event_type"],
                "state": None,
                "attributes": attributes,
                "origin": ORIGIN,
            }
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse event row: {e}")
            return None

    def _parse_state_row(self, row: sqlite3.Row) -> Optional[dict[str, Any]]:
        """Parse a database row into a state dictionary."""
        try:
            # Parse timestamp from Unix timestamp
            timestamp = datetime.fromtimestamp(row["last_updated_ts"], tz=timezone.utc).isoformat()
            
            # Parse attributes JSON
            attributes = {}
            if row["attributes"]:
                try:
                    attributes = json.loads(row["attributes"])
                except json.JSONDecodeError:
                    pass
            
            return {
                "id": row["state_id"],
                "type": "state",
                "timestamp": timestamp,
                "entity_id": row["entity_id"] or "",
                "event_type": "state_changed",
                "state": row["state"],
                "attributes": attributes,
                "origin": ORIGIN,
            }
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse state row: {e}")
            return None


class CloudApiClient:
    """Handles communication with the remote Cloud API."""

    def __init__(
        self,
        api_endpoint: str = API_ENDPOINT,
        auth_token: str = CLOUD_AUTH_TOKEN,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ):
        self.api_endpoint = api_endpoint
        self.auth_token = auth_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a reusable HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def send_batch(self, records: list[dict[str, Any]]) -> bool:
        """
        Send a batch of records to the Cloud API.

        Returns True if successful, False otherwise.
        """
        if not records:
            return True

        if not self.auth_token:
            logger.error("No authentication token configured. Skipping API call.")
            return False

        if self.api_endpoint == DEFAULT_API_ENDPOINT:
            logger.error("API endpoint is still the default placeholder. Configure a real endpoint.")
            return False

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json",
        }

        payload = {
            "records": records,
            "source": "home_assistant",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    self.api_endpoint,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status == 200 or response.status == 201:
                        logger.info(f"Successfully sent {len(records)} records to API")
                        return True
                    elif response.status >= 500:
                        logger.warning(
                            f"Server error {response.status} on attempt {attempt}/{MAX_RETRIES}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"API error {response.status}: {error_text}"
                        )
                        return False

            except asyncio.TimeoutError:
                logger.warning(f"Request timeout on attempt {attempt}/{MAX_RETRIES}")
            except aiohttp.ClientError as e:
                logger.warning(f"Network error on attempt {attempt}/{MAX_RETRIES}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error sending to API: {e}")
                return False

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        logger.error(f"Failed to send batch after {MAX_RETRIES} attempts")
        return False


class EventExtractor:
    """Main orchestrator for the event extraction process."""

    def __init__(self):
        self.checkpoint_manager = CheckpointManager()
        self.db_reader = DatabaseReader()
        self.api_client = CloudApiClient()
        self.running = True

    async def run(self) -> None:
        """Main run loop."""
        logger.info("Life Emotions HA Plugin started")
        logger.info(f"Database path: {DATABASE_PATH}")
        logger.info(f"API endpoint: {API_ENDPOINT}")
        logger.info(f"Sync interval: {SYNC_INTERVAL_MINUTES} minutes")
        logger.info(f"Batch size: {BATCH_SIZE}")
        token_status = f"***{CLOUD_AUTH_TOKEN[-4:]}" if len(CLOUD_AUTH_TOKEN) > 4 else ("set (short)" if CLOUD_AUTH_TOKEN else "NOT SET")
        logger.info(f"Auth token: {token_status}")

        if API_ENDPOINT == DEFAULT_API_ENDPOINT:
            logger.warning("API endpoint is the default placeholder — data will NOT be sent until configured")

        # Validate database exists
        if not Path(DATABASE_PATH).exists():
            logger.error(f"Database not found at {DATABASE_PATH}")
            logger.error("Make sure the add-on has access to the config directory")
            return

        try:
            while self.running:
                try:
                    await self.sync_cycle()
                except Exception as e:
                    logger.error(f"Error in sync cycle: {e}")

                if not self.running:
                    break

                logger.info(f"Sleeping for {SYNC_INTERVAL_MINUTES} minutes...")
                try:
                    await asyncio.sleep(SYNC_INTERVAL_MINUTES * 60)
                except asyncio.CancelledError:
                    break
        finally:
            await self.api_client.close()

    async def sync_cycle(self) -> None:
        """Perform one sync cycle: fetch and send data."""
        checkpoint = self.checkpoint_manager.load()
        last_event_id = checkpoint.get("last_event_id", 0)
        last_state_id = checkpoint.get("last_state_id", 0)

        logger.info(f"Starting sync cycle from event_id={last_event_id}, state_id={last_state_id}")

        # Process events
        last_event_id = await self._process_events(last_event_id)

        # Process states
        last_state_id = await self._process_states(last_state_id)

        # Save checkpoint once at the end of the cycle
        self.checkpoint_manager.save(last_event_id, last_state_id)

    async def _process_events(self, last_event_id: int) -> int:
        """Process events in batches. Returns the latest processed event ID."""
        current_event_id = last_event_id

        while True:
            events = self.db_reader.fetch_events(current_event_id, BATCH_SIZE)

            if not events:
                logger.info("No new events to process")
                break

            success = await self.api_client.send_batch(events)

            if success:
                current_event_id = max(e["id"] for e in events)
                logger.info(f"Processed events up to id={current_event_id}")
            else:
                logger.warning("Failed to send events batch, will retry next cycle")
                break

            # If we got fewer than batch_size, we've reached the end
            if len(events) < BATCH_SIZE:
                break

        return current_event_id

    async def _process_states(self, last_state_id: int) -> int:
        """Process states in batches. Returns the latest processed state ID."""
        current_state_id = last_state_id

        while True:
            states = self.db_reader.fetch_states(current_state_id, BATCH_SIZE)

            if not states:
                logger.info("No new states to process")
                break

            success = await self.api_client.send_batch(states)

            if success:
                current_state_id = max(s["id"] for s in states)
                logger.info(f"Processed states up to id={current_state_id}")
            else:
                logger.warning("Failed to send states batch, will retry next cycle")
                break

            # If we got fewer than batch_size, we've reached the end
            if len(states) < BATCH_SIZE:
                break

        return current_state_id


def main() -> None:
    """Entry point for the add-on."""
    extractor = EventExtractor()

    loop = asyncio.new_event_loop()

    def _shutdown_handler() -> None:
        logger.info("Shutdown signal received, stopping...")
        extractor.running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_handler)

    try:
        loop.run_until_complete(extractor.run())
    finally:
        loop.close()
        logger.info("Life Emotions HA Plugin stopped")


if __name__ == "__main__":
    main()
