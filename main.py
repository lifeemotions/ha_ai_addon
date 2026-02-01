#!/usr/bin/env python3
"""
Life Emotions AI

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
    CLOUD_AUTH_TOKEN,
    DATABASE_PATH,
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
logger = logging.getLogger("lifeemotions_ai_addon")


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

    def fetch_events(self, after_timestamp: float, batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
        """
        Fetch events from the database with time_fired_ts > after_timestamp.

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
                        WHERE e.time_fired_ts > ?
                        ORDER BY e.time_fired_ts ASC
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
                        WHERE e.time_fired_ts > ?
                        ORDER BY e.time_fired_ts ASC
                        LIMIT ?
                    """

                cursor.execute(query, (after_timestamp, batch_size))
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

    def fetch_states(self, after_timestamp: float, batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
        """
        Fetch states from the database with last_updated_ts > after_timestamp.

        Uses the newer HA database schema where attributes are in a separate table.
        """
        states = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

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
                    WHERE s.last_updated_ts > ?
                    ORDER BY s.last_updated_ts ASC
                    LIMIT ?
                """
                cursor.execute(query, (after_timestamp, batch_size))
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
            raw_ts = row["time_fired_ts"]
            timestamp = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()

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
                "raw_timestamp": raw_ts,
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
            raw_ts = row["last_updated_ts"]
            timestamp = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()

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
                "raw_timestamp": raw_ts,
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

    async def fetch_checkpoint(self) -> Optional[float]:
        """
        Fetch the checkpoint timestamp from the Cloud API.

        Returns the last_timestamp (Unix float) on success, or None on failure.
        """
        if not self.auth_token:
            logger.error("No authentication token configured. Cannot fetch checkpoint.")
            return None

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                session = await self._get_session()
                async with session.get(
                    self.api_endpoint,
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        last_ts = data.get("last_timestamp")
                        if last_ts is not None:
                            last_ts = float(last_ts)
                            logger.info(f"Fetched checkpoint from API: last_timestamp={last_ts}")
                            return last_ts
                        else:
                            logger.error("API response missing 'last_timestamp' field")
                            return None
                    elif response.status >= 500:
                        logger.warning(
                            f"Server error {response.status} fetching checkpoint on attempt {attempt}/{MAX_RETRIES}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"API error {response.status} fetching checkpoint: {error_text}"
                        )
                        return None

            except asyncio.TimeoutError:
                logger.warning(f"Checkpoint request timeout on attempt {attempt}/{MAX_RETRIES}")
            except aiohttp.ClientError as e:
                logger.warning(f"Network error fetching checkpoint on attempt {attempt}/{MAX_RETRIES}: {e}")
            except (ValueError, KeyError) as e:
                logger.error(f"Invalid checkpoint response from API: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error fetching checkpoint: {e}")
                return None

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        logger.error(f"Failed to fetch checkpoint after {MAX_RETRIES} attempts")
        return None

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
        self.db_reader = DatabaseReader()
        self.api_client = CloudApiClient()
        self.running = True

    async def run(self) -> None:
        """Main run loop."""
        logger.info("Life Emotions AI started")
        logger.info(f"Database path: {DATABASE_PATH}")
        logger.info(f"API endpoint: {API_ENDPOINT}")
        logger.info(f"Sync interval: {SYNC_INTERVAL_MINUTES} minutes")
        logger.info(f"Batch size: {BATCH_SIZE}")
        token_status = f"***{CLOUD_AUTH_TOKEN[-4:]}" if len(CLOUD_AUTH_TOKEN) > 4 else ("set (short)" if CLOUD_AUTH_TOKEN else "NOT SET")
        logger.info(f"Auth token: {token_status}")

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
        """Perform one sync cycle: fetch checkpoint from API, then fetch and send data."""
        last_timestamp = await self.api_client.fetch_checkpoint()

        if last_timestamp is None:
            logger.warning("Could not fetch checkpoint from API, skipping sync cycle")
            return

        logger.info(f"Starting sync cycle from timestamp={last_timestamp}")

        # Process events
        await self._process_events(last_timestamp)

        # Process states
        await self._process_states(last_timestamp)

    async def _process_events(self, after_timestamp: float) -> float:
        """Process events in batches. Returns the latest processed timestamp."""
        current_timestamp = after_timestamp

        while True:
            events = self.db_reader.fetch_events(current_timestamp, BATCH_SIZE)

            if not events:
                logger.info("No new events to process")
                break

            success = await self.api_client.send_batch(events)

            if success:
                current_timestamp = max(e["raw_timestamp"] for e in events)
                logger.info(f"Processed events up to timestamp={current_timestamp}")
            else:
                logger.warning("Failed to send events batch, will retry next cycle")
                break

            # If we got fewer than batch_size, we've reached the end
            if len(events) < BATCH_SIZE:
                break

        return current_timestamp

    async def _process_states(self, after_timestamp: float) -> float:
        """Process states in batches. Returns the latest processed timestamp."""
        current_timestamp = after_timestamp

        while True:
            states = self.db_reader.fetch_states(current_timestamp, BATCH_SIZE)

            if not states:
                logger.info("No new states to process")
                break

            success = await self.api_client.send_batch(states)

            if success:
                current_timestamp = max(s["raw_timestamp"] for s in states)
                logger.info(f"Processed states up to timestamp={current_timestamp}")
            else:
                logger.warning("Failed to send states batch, will retry next cycle")
                break

            # If we got fewer than batch_size, we've reached the end
            if len(states) < BATCH_SIZE:
                break

        return current_timestamp


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
        logger.info("Life Emotions AI stopped")


if __name__ == "__main__":
    main()
