#!/usr/bin/env python3
"""
Life Emotions HA Plugin

Extracts historical device events from the local SQLite database
and streams them to a remote Cloud API.
"""

import asyncio
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiohttp

from const import (
    API_ENDPOINT,
    BATCH_SIZE,
    CHECKPOINT_FILE,
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

    def _get_connection(self) -> sqlite3.Connection:
        """Create a read-only database connection."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch_events(self, last_event_id: int, batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
        """
        Fetch events from the database starting after last_event_id.
        
        Uses the newer HA database schema where event_data is in a separate table.
        """
        events = []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Query events with joined event_data (HA 2022.6+ schema)
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
            
            conn.close()
            logger.info(f"Fetched {len(events)} events from database")
            
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
            conn = self._get_connection()
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
            
            conn.close()
            logger.info(f"Fetched {len(states)} states from database")
            
        except sqlite3.Error as e:
            logger.error(f"Database error fetching states: {e}")
        
        return states

    def _parse_event_row(self, row: sqlite3.Row) -> Optional[dict[str, Any]]:
        """Parse a database row into an event dictionary."""
        try:
            # Parse timestamp from Unix timestamp
            timestamp = datetime.utcfromtimestamp(row["time_fired_ts"]).isoformat() + "Z"
            
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
            timestamp = datetime.utcfromtimestamp(row["last_updated_ts"]).isoformat() + "Z"
            
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
            "sent_at": datetime.utcnow().isoformat() + "Z",
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
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
                logger.info(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")
                await asyncio.sleep(RETRY_DELAY_SECONDS)

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

        # Validate database exists
        if not Path(DATABASE_PATH).exists():
            logger.error(f"Database not found at {DATABASE_PATH}")
            logger.error("Make sure the add-on has access to the config directory")
            return

        while self.running:
            try:
                await self.sync_cycle()
            except Exception as e:
                logger.error(f"Error in sync cycle: {e}")

            logger.info(f"Sleeping for {SYNC_INTERVAL_MINUTES} minutes...")
            await asyncio.sleep(SYNC_INTERVAL_MINUTES * 60)

    async def sync_cycle(self) -> None:
        """Perform one sync cycle: fetch and send data."""
        checkpoint = self.checkpoint_manager.load()
        last_event_id = checkpoint.get("last_event_id", 0)
        last_state_id = checkpoint.get("last_state_id", 0)

        logger.info(f"Starting sync cycle from event_id={last_event_id}, state_id={last_state_id}")

        # Process events
        await self._process_events(last_event_id)
        
        # Process states
        await self._process_states(last_state_id)

    async def _process_events(self, last_event_id: int) -> None:
        """Process events in batches."""
        current_event_id = last_event_id
        
        while True:
            events = self.db_reader.fetch_events(current_event_id, BATCH_SIZE)
            
            if not events:
                logger.info("No new events to process")
                break

            success = await self.api_client.send_batch(events)
            
            if success:
                # Update checkpoint with the last processed event ID
                new_event_id = max(e["id"] for e in events)
                checkpoint = self.checkpoint_manager.load()
                self.checkpoint_manager.save(new_event_id, checkpoint.get("last_state_id", 0))
                current_event_id = new_event_id
                logger.info(f"Updated event checkpoint to {new_event_id}")
            else:
                logger.warning("Failed to send events batch, will retry next cycle")
                break

            # If we got fewer than batch_size, we've reached the end
            if len(events) < BATCH_SIZE:
                break

    async def _process_states(self, last_state_id: int) -> None:
        """Process states in batches."""
        current_state_id = last_state_id
        
        while True:
            states = self.db_reader.fetch_states(current_state_id, BATCH_SIZE)
            
            if not states:
                logger.info("No new states to process")
                break

            success = await self.api_client.send_batch(states)
            
            if success:
                # Update checkpoint with the last processed state ID
                new_state_id = max(s["id"] for s in states)
                checkpoint = self.checkpoint_manager.load()
                self.checkpoint_manager.save(checkpoint.get("last_event_id", 0), new_state_id)
                current_state_id = new_state_id
                logger.info(f"Updated state checkpoint to {new_state_id}")
            else:
                logger.warning("Failed to send states batch, will retry next cycle")
                break

            # If we got fewer than batch_size, we've reached the end
            if len(states) < BATCH_SIZE:
                break


def main() -> None:
    """Entry point for the add-on."""
    extractor = EventExtractor()
    
    try:
        asyncio.run(extractor.run())
    except KeyboardInterrupt:
        logger.info("Shutting down Life Emotions HA Plugin...")
        extractor.running = False


if __name__ == "__main__":
    main()
