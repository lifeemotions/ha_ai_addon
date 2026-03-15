"""Integration tests for installing the add-on into Home Assistant Supervisor.

Usage:
    .venv/bin/python -m pytest tests/test_integration_addon_install.py -v

Requires Docker to be running. Uses HA Supervised (with Supervisor) to test
actual add-on installation flow.

Note: These tests are slower than the database tests as they spin up a full
HA Supervised environment with Docker-in-Docker.
"""

import subprocess
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# pytest-docker configuration
# ---------------------------------------------------------------------------

ADDON_SLUG = "local_lifeemotions_ai_addon"


@pytest.fixture(scope="session")
def docker_compose_file():
    """Point pytest-docker at our HA Supervisor compose file (overrides conftest.py)."""
    return str(Path(__file__).parent / "docker-compose.ha-supervisor.yml")


@pytest.fixture(scope="session")
def outer_container_name(docker_compose_project_name):
    """Get the name of the outer container (the devcontainer)."""
    return f"{docker_compose_project_name}-homeassistant-supervisor-1"


def run_docker_exec(container_name: str, cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command inside the outer container."""
    full_cmd = f"docker exec {container_name} {cmd}"
    return subprocess.run(
        full_cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_hassio_cli(container_name: str, cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command using the hassio_cli container."""
    return run_docker_exec(container_name, f"docker exec hassio_cli ha {cmd}", timeout=timeout)


def is_supervisor_ready(container_name: str) -> bool:
    """Check if the Supervisor is ready by checking if the CLI container is running."""
    result = run_docker_exec(container_name, "docker ps --format '{{.Names}}'")
    return "hassio_cli" in result.stdout


def wait_for_supervisor(container_name: str, timeout: int = 300):
    """Wait for the Supervisor and CLI to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        if is_supervisor_ready(container_name):
            # Give it a few more seconds to stabilize
            time.sleep(5)
            return True
        time.sleep(5)
    return False


@pytest.fixture(scope="session")
def supervisor_ready(docker_services, docker_ip, outer_container_name):
    """Wait for HA Supervisor to be ready."""
    # pytest-docker starts the compose file, but we need to wait for
    # the inner Docker containers to be ready
    if not wait_for_supervisor(outer_container_name, timeout=300):
        pytest.fail("Supervisor did not become ready within 5 minutes")

    # Wait for store to be populated
    for _ in range(30):
        result = run_hassio_cli(outer_container_name, "store")
        if "Life Emotions AI" in result.stdout:
            return outer_container_name
        time.sleep(5)

    pytest.fail("Add-on not found in store after waiting")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
class TestSupervisorIsRunning:
    """Verify the HA Supervisor environment is up and running."""

    def test_supervisor_info(self, supervisor_ready):
        """Supervisor info should be available via CLI."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, "supervisor info")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert "version" in result.stdout.lower()

    def test_docker_containers_running(self, supervisor_ready):
        """Required containers should be running."""
        container_name = supervisor_ready
        result = run_docker_exec(container_name, "docker ps --format '{{.Names}}'")
        assert result.returncode == 0

        required_containers = ["hassio_supervisor", "hassio_cli", "hassio_dns"]
        for container in required_containers:
            assert container in result.stdout, f"Container {container} not running"


@pytest.mark.integration
@pytest.mark.slow
class TestLocalAddonRepository:
    """Test that our add-on appears in the local repository."""

    def test_addon_visible_in_store(self, supervisor_ready):
        """Our add-on should be visible in the local add-ons store."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, "store")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Check our add-on is in the store
        assert "Life Emotions AI" in result.stdout, (
            f"Add-on not found in store output:\n{result.stdout[:1000]}"
        )
        assert ADDON_SLUG in result.stdout, (
            f"Add-on slug not found in store output:\n{result.stdout[:1000]}"
        )

    def test_addon_info_available(self, supervisor_ready):
        """We should be able to get info about our add-on."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, f"addon info {ADDON_SLUG}")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Verify basic add-on metadata
        assert "name: Life Emotions AI" in result.stdout
        assert "version_latest: 1.0.2" in result.stdout
        assert "amd64" in result.stdout or "aarch64" in result.stdout


@pytest.mark.integration
@pytest.mark.slow
class TestAddonInstallation:
    """Test installing our add-on via the Supervisor CLI."""

    def test_install_addon(self, supervisor_ready):
        """Installing the add-on should succeed."""
        container_name = supervisor_ready
        # Install the add-on (may take a while to build)
        result = run_hassio_cli(container_name, f"addon install {ADDON_SLUG}", timeout=180)

        # Check for success (ignoring deprecation warnings)
        assert result.returncode == 0 or "Command completed successfully" in result.stdout, (
            f"Install failed: {result.stderr}\n{result.stdout}"
        )

    def test_addon_shows_as_installed(self, supervisor_ready):
        """After installation, the add-on should appear in installed add-ons."""
        container_name = supervisor_ready
        # Wait a bit for installation to complete
        time.sleep(5)

        result = run_hassio_cli(container_name, f"addon info {ADDON_SLUG}")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Verify the add-on is installed (has a version, not just version_latest)
        assert "version: 1.0.2" in result.stdout, (
            f"Add-on version not found - may not be installed:\n{result.stdout}"
        )

    def test_installed_addons_list_includes_ours(self, supervisor_ready):
        """The global installed add-ons list should include our add-on."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, "addons")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Our add-on should be in the installed list
        assert ADDON_SLUG in result.stdout, (
            f"Add-on not in installed list:\n{result.stdout}"
        )
        assert "Life Emotions AI" in result.stdout


@pytest.mark.integration
@pytest.mark.slow
class TestAddonConfiguration:
    """Test that our add-on configuration is correct."""

    def test_addon_has_expected_options(self, supervisor_ready):
        """The add-on should have our configuration options."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, f"addon info {ADDON_SLUG}")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Check for our configuration options
        assert "cloud_auth_token" in result.stdout

    def test_addon_options_no_longer_include_removed_fields(self, supervisor_ready):
        """sync_interval_minutes and batch_size should not be in config options."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, f"addon info {ADDON_SLUG}")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        assert "sync_interval_minutes" not in result.stdout
        assert "batch_size" not in result.stdout

    def test_addon_requires_correct_ha_version(self, supervisor_ready):
        """The add-on should require HA 2023.4.0 or later."""
        container_name = supervisor_ready
        result = run_hassio_cli(container_name, f"addon info {ADDON_SLUG}")
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Check for minimum HA version requirement
        assert "homeassistant: 2023.4.0" in result.stdout


@pytest.mark.integration
@pytest.mark.slow
class TestAddonStartup:
    """Test that the add-on starts and logs expected output."""

    def test_addon_starts_and_logs_no_token(self, supervisor_ready):
        """Starting the addon with no token should log an auth error and exit."""
        container_name = supervisor_ready

        # Start the add-on (default config has empty cloud_auth_token)
        result = run_hassio_cli(container_name, f"addon start {ADDON_SLUG}", timeout=60)
        assert result.returncode == 0 or "Command completed successfully" in result.stdout, (
            f"Start failed: {result.stderr}\n{result.stdout}"
        )

        # Wait for the add-on to start and produce logs
        time.sleep(15)

        # Check the logs for the expected "no token" message
        result = run_hassio_cli(container_name, f"addon logs {ADDON_SLUG}", timeout=30)
        assert result.returncode == 0, f"Logs failed: {result.stderr}"

        assert "No authentication token configured" in result.stdout, (
            f"Expected 'no token' log message not found:\n{result.stdout[:2000]}"
        )
