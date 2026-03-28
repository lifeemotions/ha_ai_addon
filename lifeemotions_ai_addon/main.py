#!/usr/bin/env python3
"""
Life Emotions AI

Extracts historical device events from the local SQLite database
and streams them to a remote Cloud API.
"""

import asyncio
import fnmatch
import json
import logging
import shutil
import signal
import sqlite3
import sys
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from ha_api import check_recorder_dialect
from const import (
    AGGREGATION_BUCKET_SECONDS,
    API_ENDPOINT,
    CLOUD_AUTH_TOKEN,
    CONFIG_FILE_PATH,
    CONFIG_REFRESH_INTERVAL_MINUTES,
    DATABASE_PATH,
    DEFAULT_EXCLUDE_ENTITY_PREFIXES,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_RETRIES,
    MODEL_DIR,
    MODEL_DOWNLOAD_TIMEOUT_SECONDS,
    MODEL_ENTRY_POINT,
    MODEL_REQUIREMENTS_FILE,
    MODEL_VERSION_FILE,
    ORIGIN,
    PREDICTION_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_DELAY_SECONDS,
)

# Configure logging for Home Assistant Supervisor log stream
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("lifeemotions_ai_addon")


class DatabaseReader:
    """Reads events and states from the Home Assistant SQLite database.

    Requires HA 2023.4+ database schema (event_types in separate table,
    state_changed events no longer recorded in events table).
    """

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Create a read-only database connection."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch_events(self, after_timestamp: float, batch_size: int = 100) -> list[dict[str, Any]]:
        """
        Fetch events from the database with time_fired_ts > after_timestamp.

        Uses HA 2023.4+ schema where event_type is in a separate event_types table.
        Note: state_changed events are no longer recorded here; state changes
        are only in the states table.
        """
        events = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

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

    def fetch_states(self, after_timestamp: float, batch_size: int = 100) -> list[dict[str, Any]]:
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


class DataProcessor:
    """Filters and aggregates records before sending to the Cloud API.

    Tier 1 — Entity filtering: hardcoded prefix exclusions + server-side config.
    Tier 2 — Numeric aggregation: 5-minute buckets, average value.
    """

    def __init__(self):
        self._include_domains: set[str] = set()
        self._exclude_entities: list[str] = []

    def update_filters(self, config: dict[str, Any]) -> None:
        """Update entity filters from remote config."""
        filters = config.get("entity_filters", {})
        self._include_domains = set(filters.get("include_domains", []))
        self._exclude_entities = filters.get("exclude_entities", [])

    def _is_entity_allowed(self, entity_id: str) -> bool:
        """Check if an entity passes filtering rules."""
        if not entity_id:
            return True

        # Hardcoded prefix exclusions (always applied)
        for prefix in DEFAULT_EXCLUDE_ENTITY_PREFIXES:
            if entity_id.startswith(prefix):
                return False

        # Server-side include_domains filter
        if self._include_domains:
            domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
            if domain not in self._include_domains:
                return False

        # Server-side exclude_entities filter (glob patterns)
        for pattern in self._exclude_entities:
            if fnmatch.fnmatch(entity_id, pattern):
                return False

        return True

    def _aggregate_numeric_states(self, states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate numeric states into 5-minute buckets (avg value).

        Non-numeric states pass through unchanged.
        """
        numeric_buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}
        non_numeric: list[dict[str, Any]] = []

        for record in states:
            state_val = record.get("state")
            try:
                float(state_val)
                is_numeric = True
            except (ValueError, TypeError):
                is_numeric = False

            if is_numeric:
                entity_id = record["entity_id"]
                bucket = int(record["raw_timestamp"] // AGGREGATION_BUCKET_SECONDS)
                key = (entity_id, bucket)
                numeric_buckets.setdefault(key, []).append(record)
            else:
                non_numeric.append(record)

        aggregated: list[dict[str, Any]] = []
        for (_entity_id, _bucket), records in numeric_buckets.items():
            values = [float(r["state"]) for r in records]
            avg_val = sum(values) / len(values)
            template = records[-1]  # use last record as template
            max_ts = max(r["raw_timestamp"] for r in records)

            aggregated.append({
                "type": "state",
                "timestamp": datetime.fromtimestamp(max_ts, tz=timezone.utc).isoformat(),
                "raw_timestamp": max_ts,
                "entity_id": template["entity_id"],
                "event_type": "state_changed",
                "state": str(round(avg_val, 4)),
                "attributes": template.get("attributes") or {},
                "origin": template.get("origin", ORIGIN),
            })

        result = non_numeric + aggregated
        result.sort(key=lambda r: r["raw_timestamp"])
        return result

    def process_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply entity filtering to events."""
        filtered = [e for e in events if self._is_entity_allowed(e.get("entity_id", ""))]
        dropped = len(events) - len(filtered)
        if dropped:
            logger.info(f"Entity filter: events {len(events)} -> {len(filtered)} ({dropped} excluded)")
        return filtered

    def process_states(self, states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply entity filtering + numeric aggregation to states."""
        # Tier 1: filter
        filtered = [s for s in states if self._is_entity_allowed(s.get("entity_id", ""))]
        filter_dropped = len(states) - len(filtered)

        # Tier 2: aggregate numeric
        result = self._aggregate_numeric_states(filtered)
        agg_reduced = len(filtered) - len(result)

        if filter_dropped or agg_reduced:
            logger.info(
                f"States processed: {len(states)} raw, "
                f"{filter_dropped} filtered, {agg_reduced} aggregated, "
                f"{len(result)} to send"
            )
        return result


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

    async def fetch_checkpoint(self) -> Optional[dict[str, float]]:
        """
        Fetch checkpoint timestamps from the Cloud API.

        Returns a dict with 'event' and 'state' cursor timestamps,
        or None on failure.
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
                    f"{self.api_endpoint}/data",
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()

                        # New format: separate cursors per type
                        event_ts = data.get("last_event_timestamp")
                        state_ts = data.get("last_state_timestamp")

                        if event_ts is not None and state_ts is not None:
                            result = {"event": float(event_ts), "state": float(state_ts)}
                            logger.info(f"Fetched checkpoint from API: event={result['event']}, state={result['state']}")
                            return result

                        # Fallback: old server returning only last_timestamp
                        last_ts = data.get("last_timestamp")
                        if last_ts is not None:
                            ts = float(last_ts)
                            logger.info(f"Fetched checkpoint from API (legacy): last_timestamp={ts}")
                            return {"event": ts, "state": ts}

                        logger.error("API response missing checkpoint fields")
                        return None
                    elif response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                        logger.warning(
                            f"Rate limited (429) fetching checkpoint, retrying after {retry_after}s (attempt {attempt}/{MAX_RETRIES})"
                        )
                        await asyncio.sleep(retry_after)
                        continue
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
                    f"{self.api_endpoint}/data",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status == 200 or response.status == 201:
                        logger.info(f"Successfully sent {len(records)} records to API")
                        return True
                    elif response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                        logger.warning(
                            f"Rate limited (429) sending batch, retrying after {retry_after}s (attempt {attempt}/{MAX_RETRIES})"
                        )
                        await asyncio.sleep(retry_after)
                        continue
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

    async def verify_token(self) -> Optional[dict[str, Any]]:
        """
        Verify the auth token with the Cloud API via POST /verify.

        Returns {"sync_interval_minutes": N, "batch_size": N} on success,
        or None on failure.
        Fail-fast on 401/403/404 (no retry).
        Retries on 5xx/timeout/network errors.
        """
        if not self.auth_token:
            logger.error("No authentication token configured. Cannot verify.")
            return None

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                session = await self._get_session()
                async with session.post(
                    f"{self.api_endpoint}/verify",
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = {
                            "sync_interval_minutes": data["sync_interval_minutes"],
                            "batch_size": data["batch_size"],
                        }
                        logger.info(f"Token verified: sync_interval={result['sync_interval_minutes']}min, batch_size={result['batch_size']}")
                        return result
                    elif response.status in (401, 403, 404):
                        error_text = await response.text()
                        logger.error(
                            f"Token verification failed ({response.status}): {error_text}"
                        )
                        return None
                    elif response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                        logger.warning(
                            f"Rate limited (429) verifying token, retrying after {retry_after}s (attempt {attempt}/{MAX_RETRIES})"
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    elif response.status >= 500:
                        logger.warning(
                            f"Server error {response.status} verifying token on attempt {attempt}/{MAX_RETRIES}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"API error {response.status} verifying token: {error_text}"
                        )
                        return None

            except asyncio.TimeoutError:
                logger.warning(f"Verify request timeout on attempt {attempt}/{MAX_RETRIES}")
            except aiohttp.ClientError as e:
                logger.warning(f"Network error verifying token on attempt {attempt}/{MAX_RETRIES}: {e}")
            except (KeyError, ValueError) as e:
                logger.error(f"Invalid verify response from API: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error verifying token: {e}")
                return None

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        logger.error(f"Failed to verify token after {MAX_RETRIES} attempts")
        return None

    async def fetch_config(self) -> Optional[dict[str, Any]]:
        """
        Fetch remote configuration from the Cloud API.

        Returns the config dict on success, or None on failure.
        """
        if not self.auth_token:
            logger.error("No authentication token configured. Cannot fetch config.")
            return None

        headers = {
            "Authorization": f"Bearer {self.auth_token}",
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                session = await self._get_session()
                async with session.get(
                    f"{self.api_endpoint}/config",
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"Fetched remote config from API: {list(data.keys())}")
                        return data
                    elif response.status == 429:
                        retry_after = int(response.headers.get("Retry-After", RETRY_DELAY_SECONDS))
                        logger.warning(
                            f"Rate limited (429) fetching config, retrying after {retry_after}s (attempt {attempt}/{MAX_RETRIES})"
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    elif response.status >= 500:
                        logger.warning(
                            f"Server error {response.status} fetching config on attempt {attempt}/{MAX_RETRIES}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"API error {response.status} fetching config: {error_text}"
                        )
                        return None

            except asyncio.TimeoutError:
                logger.warning(f"Config request timeout on attempt {attempt}/{MAX_RETRIES}")
            except aiohttp.ClientError as e:
                logger.warning(f"Network error fetching config on attempt {attempt}/{MAX_RETRIES}: {e}")
            except (ValueError, KeyError) as e:
                logger.error(f"Invalid config response from API: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error fetching config: {e}")
                return None

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        logger.error(f"Failed to fetch config after {MAX_RETRIES} attempts")
        return None


class ModelManager:
    """Manages downloading, installing, and executing the XGBoost prediction model."""

    def __init__(self, api_client: CloudApiClient):
        self.api_client = api_client
        self.model_dir = Path(MODEL_DIR)
        self.version_file = Path(MODEL_VERSION_FILE)
        self._installed_version: Optional[str] = self._load_installed_version()
        self._last_prediction_wall_time: Optional[datetime] = None

    # --- Version tracking ---

    def _load_installed_version(self) -> Optional[str]:
        """Read installed model ID from local version.json."""
        if not self.version_file.exists():
            return None
        try:
            with open(self.version_file) as f:
                data = json.load(f)
            # Support both new (installed_model_id) and legacy (installed_model_version) keys
            model_id = data.get("installed_model_id") or data.get("installed_model_version")
            if model_id:
                logger.info(f"Loaded installed model ID: {model_id}")
            return model_id
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load model version file: {e}")
            return None

    def _save_installed_version(self, version: str) -> None:
        """Write installed model ID to local version.json."""
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            with open(self.version_file, "w") as f:
                json.dump({"installed_model_id": version}, f, indent=2)
            logger.info(f"Saved installed model ID: {version}")
        except OSError as e:
            logger.warning(f"Failed to save model version file: {e}")

    def get_installed_version(self) -> Optional[str]:
        """Get the currently installed model version."""
        return self._installed_version

    # --- Download + Install ---

    async def check_and_update(self, config: dict[str, Any]) -> bool:
        """Compare config model_id to installed. Download if different.

        Returns True if model is ready (either already current or newly installed).
        Returns False if no model is available.
        """
        # Support both new (model_id) and legacy (model_version) config keys
        remote_version = config.get("model_id") or config.get("model_version")
        if not remote_version:
            logger.info("No model_id in config, skipping model update")
            return self.has_model()

        if remote_version == self._installed_version:
            logger.debug(f"Model already at {remote_version}")
            return True

        logger.info(f"Model update available: {self._installed_version} -> {remote_version}")

        archive_path = await self._download_model(remote_version)
        if archive_path is None:
            return self.has_model()

        if not self._extract_archive(archive_path):
            return self.has_model()

        if not await self._install_dependencies():
            logger.error("Failed to install model dependencies")
            return False

        self._installed_version = remote_version
        self._save_installed_version(remote_version)
        logger.info(f"Model updated to {remote_version}")
        return True

    async def _download_model(self, model_id: str) -> Optional[Path]:
        """Download model archive from API. Returns path to archive file or None."""
        if not self.api_client.auth_token:
            logger.error("No auth token, cannot download model")
            return None

        headers = {"Authorization": f"Bearer {self.api_client.auth_token}"}
        url = f"{self.api_client.api_endpoint}/model/{model_id}"
        timeout = aiohttp.ClientTimeout(total=MODEL_DOWNLOAD_TIMEOUT_SECONDS)

        self.model_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.model_dir / f"model-{model_id}.archive"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                session = await self.api_client._get_session()
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        with open(archive_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"Downloaded model {model_id} ({archive_path.stat().st_size} bytes)")
                        return archive_path
                    elif response.status == 404:
                        logger.error(f"Model {model_id} not found on server (404)")
                        return None
                    elif response.status >= 500:
                        logger.warning(
                            f"Server error {response.status} downloading model, attempt {attempt}/{MAX_RETRIES}"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(f"API error {response.status} downloading model: {error_text}")
                        return None

            except asyncio.TimeoutError:
                logger.warning(f"Model download timeout on attempt {attempt}/{MAX_RETRIES}")
            except aiohttp.ClientError as e:
                logger.warning(f"Network error downloading model on attempt {attempt}/{MAX_RETRIES}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error downloading model: {e}")
                return None

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)

        logger.error(f"Failed to download model after {MAX_RETRIES} attempts")
        return None

    def _extract_archive(self, archive_path: Path) -> bool:
        """Extract tar.gz or zip archive to model directory."""
        try:
            temp_extract_dir = self.model_dir / "_extract_tmp"
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)
            temp_extract_dir.mkdir()

            if self._is_zip_archive(archive_path):
                self._extract_zip(archive_path, temp_extract_dir)
            else:
                self._extract_tarball(archive_path, temp_extract_dir)

            # Remove old model files (except version.json and the archive itself)
            for item in self.model_dir.iterdir():
                if item.name in ("version.json", archive_path.name, "_extract_tmp"):
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

            # Move extracted files into model_dir
            # Handle case where archive has a single top-level directory
            extracted_items = list(temp_extract_dir.iterdir())
            if len(extracted_items) == 1 and extracted_items[0].is_dir():
                source_dir = extracted_items[0]
            else:
                source_dir = temp_extract_dir

            for item in source_dir.iterdir():
                dest = self.model_dir / item.name
                shutil.move(str(item), str(dest))

            shutil.rmtree(temp_extract_dir, ignore_errors=True)
            archive_path.unlink(missing_ok=True)
            logger.info("Model archive extracted successfully")
            return True

        except (tarfile.TarError, zipfile.BadZipFile, OSError) as e:
            logger.error(f"Failed to extract model archive: {e}")
            return False

    @staticmethod
    def _is_zip_archive(path: Path) -> bool:
        """Check if file is a zip archive by reading its magic bytes."""
        try:
            with open(path, "rb") as f:
                return f.read(4) == b"PK\x03\x04"
        except OSError:
            return False

    @staticmethod
    def _extract_tarball(archive_path: Path, dest: Path) -> None:
        """Extract a tar.gz archive with security filtering."""
        with tarfile.open(archive_path, "r:gz") as tar:
            safe_members = []
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    logger.warning(f"Skipping unsafe tar member: {member.name}")
                    continue
                safe_members.append(member)
            tar.extractall(path=dest, members=safe_members)

    @staticmethod
    def _extract_zip(archive_path: Path, dest: Path) -> None:
        """Extract a zip archive with security filtering."""
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    logger.warning(f"Skipping unsafe zip member: {info.filename}")
                    continue
                zf.extract(info, dest)

    async def _install_dependencies(self) -> bool:
        """Run pip install from model's requirements.txt if it exists."""
        req_file = self.model_dir / MODEL_REQUIREMENTS_FILE
        if not req_file.exists():
            logger.info("No model requirements.txt, skipping dependency install")
            return True

        logger.info("Installing model dependencies...")
        try:
            process = await asyncio.create_subprocess_exec(
                "pip3", "install", "--no-cache-dir", "-r", str(req_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info("Model dependencies installed successfully")
                return True
            else:
                logger.error(f"pip install failed (exit code {process.returncode})")
                if stderr:
                    logger.error(f"pip stderr: {stderr.decode()}")
                return False

        except (OSError, FileNotFoundError) as e:
            logger.error(f"Failed to run pip: {e}")
            return False

    # --- Prediction execution ---

    def has_model(self) -> bool:
        """Check if predict.py exists in the model directory."""
        return (self.model_dir / MODEL_ENTRY_POINT).exists()

    async def run_prediction(self) -> bool:
        """Execute predict.py as a subprocess."""
        predict_path = self.model_dir / MODEL_ENTRY_POINT
        if not predict_path.exists():
            logger.warning(f"Model entry point {MODEL_ENTRY_POINT} not found, skipping prediction")
            return False

        logger.info("Running prediction...")
        try:
            process = await asyncio.create_subprocess_exec(
                "python3", str(predict_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.model_dir),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=PREDICTION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.error(f"Prediction timed out after {PREDICTION_TIMEOUT_SECONDS}s, killing process")
                process.kill()
                await process.wait()
                return False

            if process.returncode == 0:
                logger.info("Prediction completed successfully")
                if stdout:
                    logger.info(f"Prediction output: {stdout.decode().strip()}")
                self._last_prediction_wall_time = datetime.now(timezone.utc)
                return True
            else:
                logger.error(f"Prediction failed (exit code {process.returncode})")
                if stderr:
                    logger.error(f"Prediction stderr: {stderr.decode()}")
                return False

        except (OSError, FileNotFoundError) as e:
            logger.error(f"Failed to run prediction: {e}")
            return False

    def should_run_prediction(self, schedule: str, now: Optional[datetime] = None) -> bool:
        """Evaluate whether prediction should run based on cron schedule."""
        if not schedule:
            return False
        if not self.has_model():
            return False

        try:
            from croniter import croniter
        except ImportError:
            logger.error("croniter not installed, cannot evaluate prediction schedule")
            return False

        if now is None:
            now = datetime.now(timezone.utc)

        try:
            if self._last_prediction_wall_time is None:
                # Never run before -> should run immediately
                return True

            cron = croniter(schedule, self._last_prediction_wall_time)
            next_run = cron.get_next(datetime)
            return now >= next_run

        except (ValueError, KeyError) as e:
            logger.error(f"Invalid cron schedule '{schedule}': {e}")
            return False


class EventExtractor:
    """Main orchestrator for the event extraction process."""

    def __init__(self, db_reader=None, api_client=None, model_manager=None):
        self.db_reader = db_reader or DatabaseReader()
        self.api_client = api_client or CloudApiClient()
        self.model_manager = model_manager or ModelManager(self.api_client)
        self.data_processor = DataProcessor()
        self.running = True
        self.config: Optional[dict[str, Any]] = None
        self._last_config_refresh: float = 0.0
        self.sync_interval_minutes: Optional[int] = None
        self.batch_size: Optional[int] = None
        self.verified: bool = False

    async def run(self) -> None:
        """Main run loop with two-loop architecture: heartbeat + sync."""
        logger.info("Life Emotions AI started")
        logger.info(f"Database path: {DATABASE_PATH}")
        logger.info(f"API endpoint: {API_ENDPOINT}")
        token_status = f"***{CLOUD_AUTH_TOKEN[-4:]}" if len(CLOUD_AUTH_TOKEN) > 4 else ("set (short)" if CLOUD_AUTH_TOKEN else "NOT SET")
        logger.info(f"Auth token: {token_status}")

        if not CLOUD_AUTH_TOKEN or not CLOUD_AUTH_TOKEN.strip():
            logger.error("No authentication token configured. Exiting.")
            return

        # Verify Recorder is using SQLite
        dialect = await check_recorder_dialect()
        if dialect is not None and dialect != "sqlite":
            logger.error(
                f"Home Assistant Recorder is using '{dialect}', but this add-on requires SQLite. "
                "Please configure the Recorder integration to use the default SQLite database."
            )
            return
        if dialect is None:
            logger.warning("Could not verify Recorder database type, proceeding with file check")

        # Validate database exists
        if not Path(DATABASE_PATH).exists():
            logger.error(f"Database not found at {DATABASE_PATH}")
            logger.error("Recorder may not be using SQLite, or the database has not been created yet")
            return

        try:
            await asyncio.gather(
                self._heartbeat_loop(),
                self._sync_loop(),
            )
        finally:
            await self.api_client.close()

    async def _heartbeat_loop(self) -> None:
        """Periodically verify the auth token and update sync settings."""
        while self.running:
            try:
                result = await self.api_client.verify_token()
                if result is not None:
                    self.sync_interval_minutes = result["sync_interval_minutes"]
                    self.batch_size = result["batch_size"]
                    self.verified = True
                    logger.info(f"Heartbeat OK: sync_interval={self.sync_interval_minutes}min, batch_size={self.batch_size}")
                else:
                    self.verified = False
                    logger.warning("Heartbeat failed: token verification unsuccessful")
            except Exception as e:
                self.verified = False
                logger.error(f"Error in heartbeat: {e}")

            if not self.running:
                break

            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

    async def _sync_loop(self) -> None:
        """Sync loop that waits for verification before syncing."""
        _logged_waiting = False
        while self.running:
            if not self.verified:
                if not _logged_waiting:
                    logger.info("Waiting for successful heartbeat before syncing...")
                    _logged_waiting = True
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
                continue

            _logged_waiting = False

            # Refresh config periodically
            elapsed = asyncio.get_event_loop().time() - self._last_config_refresh
            if elapsed >= CONFIG_REFRESH_INTERVAL_MINUTES * 60:
                await self._refresh_config()

            try:
                await self.sync_cycle()
            except Exception as e:
                logger.error(f"Error in sync cycle: {e}")

            # Check and run prediction if scheduled
            if self.config:
                schedule = self.config.get("prediction_schedule", "")
                if self.model_manager.should_run_prediction(schedule):
                    try:
                        await self.model_manager.run_prediction()
                    except Exception as e:
                        logger.error(f"Error running prediction: {e}")

            if not self.running:
                break

            logger.info(f"Sleeping for {self.sync_interval_minutes} minutes...")
            try:
                await asyncio.sleep(self.sync_interval_minutes * 60)
            except asyncio.CancelledError:
                break

    def _load_local_config(self) -> Optional[dict[str, Any]]:
        """Load config from local JSON file."""
        config_path = Path(CONFIG_FILE_PATH)
        if not config_path.exists():
            return None
        try:
            with open(config_path) as f:
                config = json.load(f)
            logger.info(f"Loaded local config from {CONFIG_FILE_PATH}")
            return config
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load local config: {e}")
            return None

    def _save_local_config(self, config: dict[str, Any]) -> None:
        """Save config to local JSON file."""
        try:
            with open(CONFIG_FILE_PATH, "w") as f:
                json.dump(config, f, indent=2)
            logger.info(f"Saved config to {CONFIG_FILE_PATH}")
        except OSError as e:
            logger.warning(f"Failed to save local config: {e}")

    async def _refresh_config(self) -> None:
        """Fetch config from API, falling back to local file."""
        config = await self.api_client.fetch_config()
        if config is not None:
            self.config = config
            self._save_local_config(config)
        else:
            logger.warning("Failed to fetch remote config, falling back to local config")
            local = self._load_local_config()
            if local is not None:
                self.config = local
            else:
                logger.warning("No local config available")
        self._last_config_refresh = asyncio.get_event_loop().time()

        # Update data processor filters from config
        if self.config:
            self.data_processor.update_filters(self.config)

        # Check for model updates when config is available
        if self.config:
            await self.model_manager.check_and_update(self.config)

    async def sync_cycle(self) -> None:
        """Perform one sync cycle: fetch checkpoint from API, then fetch and send data."""
        checkpoints = await self.api_client.fetch_checkpoint()

        if checkpoints is None:
            logger.warning("Could not fetch checkpoint from API, skipping sync cycle")
            return

        logger.info(f"Starting sync cycle: event_ts={checkpoints['event']}, state_ts={checkpoints['state']}")

        # Process events and states with independent cursors
        await self._process_events(checkpoints["event"])

        await self._process_states(checkpoints["state"])

    async def _process_events(self, after_timestamp: float) -> float:
        """Process events in batches. Returns the latest processed timestamp."""
        current_timestamp = after_timestamp
        batch_size = self.batch_size

        while True:
            raw_events = self.db_reader.fetch_events(current_timestamp, batch_size)

            if not raw_events:
                logger.info("No new events to process")
                break

            batch_max_ts = max(e["raw_timestamp"] for e in raw_events)
            events = self.data_processor.process_events(raw_events)

            if not events:
                current_timestamp = batch_max_ts
                if len(raw_events) < batch_size:
                    break
                continue

            success = await self.api_client.send_batch(events)

            if success:
                current_timestamp = batch_max_ts
                logger.info(f"Processed events up to timestamp={current_timestamp}")
            else:
                logger.warning("Failed to send events batch, will retry next cycle")
                break

            if len(raw_events) < batch_size:
                break

        return current_timestamp

    async def _process_states(self, after_timestamp: float) -> float:
        """Process states in batches. Returns the latest processed timestamp."""
        current_timestamp = after_timestamp
        batch_size = self.batch_size

        while True:
            raw_states = self.db_reader.fetch_states(current_timestamp, batch_size)

            if not raw_states:
                logger.info("No new states to process")
                break

            batch_max_ts = max(s["raw_timestamp"] for s in raw_states)
            states = self.data_processor.process_states(raw_states)

            if not states:
                current_timestamp = batch_max_ts
                if len(raw_states) < batch_size:
                    break
                continue

            success = await self.api_client.send_batch(states)

            if success:
                current_timestamp = batch_max_ts
                logger.info(f"Processed states up to timestamp={current_timestamp}")
            else:
                logger.warning("Failed to send states batch, will retry next cycle")
                break

            if len(raw_states) < batch_size:
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
