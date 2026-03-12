#!/usr/bin/env python3
"""
Life Emotions AI

Extracts historical device events from the local SQLite database
and streams them to a remote Cloud API.
"""

import asyncio
import json
import logging
import shutil
import signal
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from ha_api import check_recorder_dialect
from const import (
    API_ENDPOINT,
    BATCH_SIZE,
    CLOUD_AUTH_TOKEN,
    CONFIG_FILE_PATH,
    CONFIG_REFRESH_INTERVAL_MINUTES,
    DATABASE_PATH,
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

    def fetch_events(self, after_timestamp: float, batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
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
                    f"{self.api_endpoint}/data",
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
                    f"{self.api_endpoint}/data",
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
        """Read installed model version from local version.json."""
        if not self.version_file.exists():
            return None
        try:
            with open(self.version_file) as f:
                data = json.load(f)
            version = data.get("installed_model_version")
            if version:
                logger.info(f"Loaded installed model version: {version}")
            return version
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load model version file: {e}")
            return None

    def _save_installed_version(self, version: str) -> None:
        """Write installed model version to local version.json."""
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            with open(self.version_file, "w") as f:
                json.dump({"installed_model_version": version}, f, indent=2)
            logger.info(f"Saved installed model version: {version}")
        except OSError as e:
            logger.warning(f"Failed to save model version file: {e}")

    def get_installed_version(self) -> Optional[str]:
        """Get the currently installed model version."""
        return self._installed_version

    # --- Download + Install ---

    async def check_and_update(self, config: dict[str, Any]) -> bool:
        """Compare config model_version to installed. Download if different.

        Returns True if model is ready (either already current or newly installed).
        Returns False if no model is available.
        """
        remote_version = config.get("model_version")
        if not remote_version:
            logger.info("No model_version in config, skipping model update")
            return self.has_model()

        if remote_version == self._installed_version:
            logger.debug(f"Model already at version {remote_version}")
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
        logger.info(f"Model updated to version {remote_version}")
        return True

    async def _download_model(self, version: str) -> Optional[Path]:
        """Download model archive from API. Returns path to .tar.gz file or None."""
        if not self.api_client.auth_token:
            logger.error("No auth token, cannot download model")
            return None

        headers = {"Authorization": f"Bearer {self.api_client.auth_token}"}
        url = f"{self.api_client.api_endpoint}/model/{version}"
        timeout = aiohttp.ClientTimeout(total=MODEL_DOWNLOAD_TIMEOUT_SECONDS)

        self.model_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.model_dir / f"model-{version}.tar.gz"

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                session = await self.api_client._get_session()
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        with open(archive_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"Downloaded model v{version} ({archive_path.stat().st_size} bytes)")
                        return archive_path
                    elif response.status == 404:
                        logger.error(f"Model version {version} not found on server (404)")
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
        """Extract tar.gz archive to model directory."""
        try:
            temp_extract_dir = self.model_dir / "_extract_tmp"
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)
            temp_extract_dir.mkdir()

            with tarfile.open(archive_path, "r:gz") as tar:
                # Security: filter out absolute paths and path traversal
                safe_members = []
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        logger.warning(f"Skipping unsafe tar member: {member.name}")
                        continue
                    safe_members.append(member)
                tar.extractall(path=temp_extract_dir, members=safe_members)

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

        except (tarfile.TarError, OSError) as e:
            logger.error(f"Failed to extract model archive: {e}")
            return False

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
        self.running = True
        self.config: Optional[dict[str, Any]] = None
        self._last_config_refresh: float = 0.0

    async def run(self) -> None:
        """Main run loop."""
        logger.info("Life Emotions AI started")
        logger.info(f"Database path: {DATABASE_PATH}")
        logger.info(f"API endpoint: {API_ENDPOINT}")
        logger.info(f"Sync interval: {SYNC_INTERVAL_MINUTES} minutes")
        logger.info(f"Batch size: {BATCH_SIZE}")
        token_status = f"***{CLOUD_AUTH_TOKEN[-4:]}" if len(CLOUD_AUTH_TOKEN) > 4 else ("set (short)" if CLOUD_AUTH_TOKEN else "NOT SET")
        logger.info(f"Auth token: {token_status}")

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

        # Fetch remote config on startup
        await self._refresh_config()

        try:
            while self.running:
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

                logger.info(f"Sleeping for {SYNC_INTERVAL_MINUTES} minutes...")
                try:
                    await asyncio.sleep(SYNC_INTERVAL_MINUTES * 60)
                except asyncio.CancelledError:
                    break
        finally:
            await self.api_client.close()

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

        # Check for model updates when config is available
        if self.config:
            await self.model_manager.check_and_update(self.config)

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
