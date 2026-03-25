"""Tests for ModelManager class."""

import asyncio
import io
import json
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from main import ModelManager


# --- Helpers ---


def _make_mock_response(resp):
    """Create a single mock response context manager from a response dict."""
    mock_resp = MagicMock()
    mock_resp.status = resp["status"]
    if "text" in resp:
        mock_resp.text = AsyncMock(return_value=resp["text"])
    if "json" in resp:
        mock_resp.json = AsyncMock(return_value=resp["json"])
    if "content" in resp:
        mock_resp.content = resp["content"]

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_mock_session(responses, method="get"):
    """Helper to create a mock session with a sequence of responses."""
    mock_session = MagicMock()

    if not isinstance(responses, list):
        responses = [responses]

    context_managers = [_make_mock_response(r) for r in responses]

    if len(context_managers) == 1:
        setattr(mock_session, method, MagicMock(return_value=context_managers[0]))
    else:
        setattr(mock_session, method, MagicMock(side_effect=context_managers))

    return mock_session


class FakeStreamContent:
    """Fake aiohttp response content that yields chunks."""

    def __init__(self, data: bytes):
        self._data = data

    def iter_chunked(self, chunk_size: int):
        return self._iter(chunk_size)

    async def _iter(self, chunk_size: int):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]


@pytest.fixture
def make_model_archive(tmp_path):
    """Create a .tar.gz archive with given files for testing."""
    def _make(files: dict[str, str], archive_name: str = "model.tar.gz") -> Path:
        archive_path = tmp_path / archive_name
        with tarfile.open(archive_path, "w:gz") as tar:
            for name, content in files.items():
                data = content.encode()
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return archive_path
    return _make


# --- Tests ---


class TestModelManagerInit:
    """Tests for ModelManager initialization."""

    def test_init_creates_model_manager(self, tmp_path):
        api_client = AsyncMock()
        with patch("main.MODEL_DIR", str(tmp_path / "model")), \
             patch("main.MODEL_VERSION_FILE", str(tmp_path / "model" / "version.json")):
            mm = ModelManager(api_client)

        assert mm.api_client is api_client
        assert mm._installed_version is None
        assert mm._last_prediction_wall_time is None

    def test_init_loads_installed_version(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        version_file = model_dir / "version.json"
        version_file.write_text(json.dumps({"installed_model_id": "1.0.0"}))

        api_client = AsyncMock()
        with patch("main.MODEL_DIR", str(model_dir)), \
             patch("main.MODEL_VERSION_FILE", str(version_file)):
            mm = ModelManager(api_client)

        assert mm._installed_version == "1.0.0"

    def test_init_handles_missing_version_file(self, tmp_path):
        api_client = AsyncMock()
        with patch("main.MODEL_DIR", str(tmp_path / "model")), \
             patch("main.MODEL_VERSION_FILE", str(tmp_path / "model" / "version.json")):
            mm = ModelManager(api_client)

        assert mm._installed_version is None

    def test_init_handles_corrupt_version_file(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        version_file = model_dir / "version.json"
        version_file.write_text("not valid json {{")

        api_client = AsyncMock()
        with patch("main.MODEL_DIR", str(model_dir)), \
             patch("main.MODEL_VERSION_FILE", str(version_file)):
            mm = ModelManager(api_client)

        assert mm._installed_version is None


class TestLoadSaveVersion:
    """Tests for version file management."""

    def test_load_valid_version(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        version_file = tmp_path / "version.json"
        version_file.write_text(json.dumps({"installed_model_id": "2.0.0"}))
        mm.version_file = version_file

        result = mm._load_installed_version()
        assert result == "2.0.0"

    def test_load_missing_file(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.version_file = tmp_path / "nonexistent.json"

        result = mm._load_installed_version()
        assert result is None

    def test_load_invalid_json(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        version_file = tmp_path / "version.json"
        version_file.write_text("{invalid")
        mm.version_file = version_file

        result = mm._load_installed_version()
        assert result is None

    def test_load_missing_key(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        version_file = tmp_path / "version.json"
        version_file.write_text(json.dumps({"other_key": "value"}))
        mm.version_file = version_file

        result = mm._load_installed_version()
        assert result is None

    def test_save_version(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        model_dir = tmp_path / "model"
        mm.model_dir = model_dir
        mm.version_file = model_dir / "version.json"

        mm._save_installed_version("3.0.0")

        assert mm.version_file.exists()
        data = json.loads(mm.version_file.read_text())
        assert data["installed_model_id"] == "3.0.0"

    def test_save_version_creates_directory(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        model_dir = tmp_path / "new_dir" / "model"
        mm.model_dir = model_dir
        mm.version_file = model_dir / "version.json"

        mm._save_installed_version("1.0.0")

        assert model_dir.exists()
        assert mm.version_file.exists()

    def test_get_installed_version(self):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = "4.0.0"

        assert mm.get_installed_version() == "4.0.0"


class TestCheckAndUpdate:
    """Tests for ModelManager.check_and_update()."""

    @pytest.mark.asyncio
    async def test_no_model_id_in_config(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm._installed_version = None

        with patch.object(mm, "has_model", return_value=False):
            result = await mm.check_and_update({"settings": {}})

        assert result is False

    @pytest.mark.asyncio
    async def test_same_version_skips_download(self):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = "1.0.0"
        mm._download_model = AsyncMock()

        result = await mm.check_and_update({"model_id": "1.0.0"})

        assert result is True
        mm._download_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_version_triggers_full_update(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = "1.0.0"
        mm.model_dir = tmp_path / "model"
        mm.version_file = tmp_path / "model" / "version.json"

        archive = tmp_path / "archive.tar.gz"
        mm._download_model = AsyncMock(return_value=archive)
        mm._extract_archive = MagicMock(return_value=True)
        mm._install_dependencies = AsyncMock(return_value=True)
        mm._save_installed_version = MagicMock()

        result = await mm.check_and_update({"model_id": "2.0.0"})

        assert result is True
        mm._download_model.assert_called_once_with("2.0.0")
        mm._extract_archive.assert_called_once_with(archive)
        mm._install_dependencies.assert_called_once()
        mm._save_installed_version.assert_called_once_with("2.0.0")
        assert mm._installed_version == "2.0.0"

    @pytest.mark.asyncio
    async def test_download_failure_keeps_existing(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = "1.0.0"
        mm.model_dir = tmp_path / "model"

        mm._download_model = AsyncMock(return_value=None)

        with patch.object(mm, "has_model", return_value=True):
            result = await mm.check_and_update({"model_id": "2.0.0"})

        assert result is True  # has_model returns True

    @pytest.mark.asyncio
    async def test_extract_failure_keeps_existing(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = "1.0.0"
        mm.model_dir = tmp_path / "model"

        archive = tmp_path / "archive.tar.gz"
        mm._download_model = AsyncMock(return_value=archive)
        mm._extract_archive = MagicMock(return_value=False)

        with patch.object(mm, "has_model", return_value=True):
            result = await mm.check_and_update({"model_id": "2.0.0"})

        assert result is True

    @pytest.mark.asyncio
    async def test_install_deps_failure_returns_false(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = "1.0.0"
        mm.model_dir = tmp_path / "model"

        archive = tmp_path / "archive.tar.gz"
        mm._download_model = AsyncMock(return_value=archive)
        mm._extract_archive = MagicMock(return_value=True)
        mm._install_dependencies = AsyncMock(return_value=False)

        result = await mm.check_and_update({"model_id": "2.0.0"})

        assert result is False

    @pytest.mark.asyncio
    async def test_first_install_no_existing_model(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm._installed_version = None
        mm.model_dir = tmp_path / "model"
        mm.version_file = tmp_path / "model" / "version.json"

        archive = tmp_path / "archive.tar.gz"
        mm._download_model = AsyncMock(return_value=archive)
        mm._extract_archive = MagicMock(return_value=True)
        mm._install_dependencies = AsyncMock(return_value=True)
        mm._save_installed_version = MagicMock()

        result = await mm.check_and_update({"model_id": "1.0.0"})

        assert result is True
        assert mm._installed_version == "1.0.0"


class TestDownloadModel:
    """Tests for ModelManager._download_model()."""

    @pytest.mark.asyncio
    async def test_successful_download(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        archive_data = b"fake archive content"
        mock_session = _make_mock_session(
            {"status": 200, "content": FakeStreamContent(archive_data)},
        )
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        result = await mm._download_model("1.0.0")

        assert result is not None
        assert result.exists()
        assert result.read_bytes() == archive_data
        assert result.name == "model-1.0.0.archive"

        # Verify URL
        call_args = mock_session.get.call_args
        assert call_args[0][0] == "https://api.test.com/ha/model/1.0.0"

    @pytest.mark.asyncio
    async def test_download_404_returns_none(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        mock_session = _make_mock_session({"status": 404, "text": "Not Found"})
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        result = await mm._download_model("99.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_server_error_retries(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        mock_session = _make_mock_session({"status": 500})
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await mm._download_model("1.0.0")

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_download_timeout_retries(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await mm._download_model("1.0.0")

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_download_network_error_retries(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await mm._download_model("1.0.0")

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_download_no_auth_token(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = ""

        result = await mm._download_model("1.0.0")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_client_error_returns_none(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        mock_session = _make_mock_session({"status": 403, "text": "Forbidden"})
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        result = await mm._download_model("1.0.0")
        assert result is None
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_download_unexpected_error_returns_none(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.api_client = AsyncMock()
        mm.api_client.auth_token = "test-token"
        mm.api_client.api_endpoint = "https://api.test.com/ha"

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=RuntimeError("Unexpected"))
        mm.api_client._get_session = AsyncMock(return_value=mock_session)

        result = await mm._download_model("1.0.0")
        assert result is None
        assert mock_session.get.call_count == 1


class TestExtractArchive:
    """Tests for ModelManager._extract_archive()."""

    def test_extract_valid_archive(self, tmp_path, make_model_archive):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        archive = make_model_archive({
            "predict.py": "print('hello')",
            "requirements.txt": "xgboost",
            "model.json": '{"tree": []}',
        })

        # Move archive into model_dir to match real behavior
        import shutil
        dest = mm.model_dir / archive.name
        shutil.copy(str(archive), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert (mm.model_dir / "predict.py").exists()
        assert (mm.model_dir / "requirements.txt").exists()
        assert (mm.model_dir / "model.json").exists()

    def test_extract_with_single_top_level_dir(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        # Create archive with a top-level directory
        archive_path = tmp_path / "model.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            data = b"print('hello')"
            info = tarfile.TarInfo(name="model_v1/predict.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

            data2 = b"xgboost"
            info2 = tarfile.TarInfo(name="model_v1/requirements.txt")
            info2.size = len(data2)
            tar.addfile(info2, io.BytesIO(data2))

        import shutil
        dest = mm.model_dir / "model.tar.gz"
        shutil.copy(str(archive_path), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert (mm.model_dir / "predict.py").exists()
        assert (mm.model_dir / "requirements.txt").exists()

    def test_extract_filters_absolute_paths(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        archive_path = tmp_path / "evil.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            data = b"safe"
            info = tarfile.TarInfo(name="predict.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

            evil_data = b"evil"
            evil_info = tarfile.TarInfo(name="/etc/passwd")
            evil_info.size = len(evil_data)
            tar.addfile(evil_info, io.BytesIO(evil_data))

        import shutil
        dest = mm.model_dir / "evil.tar.gz"
        shutil.copy(str(archive_path), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert (mm.model_dir / "predict.py").exists()
        # The evil file should NOT have been extracted to /etc/passwd

    def test_extract_filters_path_traversal(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        archive_path = tmp_path / "evil.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            data = b"safe"
            info = tarfile.TarInfo(name="predict.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

            evil_data = b"evil"
            evil_info = tarfile.TarInfo(name="../../../evil.txt")
            evil_info.size = len(evil_data)
            tar.addfile(evil_info, io.BytesIO(evil_data))

        import shutil
        dest = mm.model_dir / "evil.tar.gz"
        shutil.copy(str(archive_path), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert not (tmp_path / "evil.txt").exists()

    def test_extract_removes_old_files(self, tmp_path, make_model_archive):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        # Create old file
        old_file = mm.model_dir / "old_predict.py"
        old_file.write_text("old code")

        archive = make_model_archive({"predict.py": "new code"})
        import shutil
        dest = mm.model_dir / archive.name
        shutil.copy(str(archive), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert not old_file.exists()
        assert (mm.model_dir / "predict.py").exists()

    def test_extract_preserves_version_json(self, tmp_path, make_model_archive):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        # Create version.json
        version_file = mm.model_dir / "version.json"
        version_file.write_text(json.dumps({"installed_model_id": "1.0.0"}))

        archive = make_model_archive({"predict.py": "new code"})
        import shutil
        dest = mm.model_dir / archive.name
        shutil.copy(str(archive), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert version_file.exists()
        data = json.loads(version_file.read_text())
        assert data["installed_model_id"] == "1.0.0"

    def test_extract_handles_corrupt_archive(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        corrupt = mm.model_dir / "corrupt.tar.gz"
        corrupt.write_bytes(b"this is not a valid tar.gz file")

        result = mm._extract_archive(corrupt)
        assert result is False

    def test_extract_zip_archive(self, tmp_path):
        import zipfile as zf
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        archive_path = tmp_path / "model.zip"
        with zf.ZipFile(archive_path, "w") as z:
            z.writestr("predict.py", "print('hello')")
            z.writestr("requirements.txt", "xgboost")
            z.writestr("model.json", '{"tree": []}')

        import shutil
        dest = mm.model_dir / archive_path.name
        shutil.copy(str(archive_path), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert (mm.model_dir / "predict.py").exists()
        assert (mm.model_dir / "requirements.txt").exists()
        assert (mm.model_dir / "model.json").exists()

    def test_extract_zip_filters_path_traversal(self, tmp_path):
        import zipfile as zf
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        archive_path = tmp_path / "evil.zip"
        with zf.ZipFile(archive_path, "w") as z:
            z.writestr("predict.py", "safe")
            z.writestr("../../../evil.txt", "evil")

        import shutil
        dest = mm.model_dir / archive_path.name
        shutil.copy(str(archive_path), str(dest))

        result = mm._extract_archive(dest)
        assert result is True
        assert (mm.model_dir / "predict.py").exists()
        assert not (tmp_path / "evil.txt").exists()


class TestInstallDependencies:
    """Tests for ModelManager._install_dependencies()."""

    @pytest.mark.asyncio
    async def test_install_success(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "requirements.txt").write_text("xgboost")

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"installed", b""))

        with patch("main.asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            result = await mm._install_dependencies()

        assert result is True
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_install_no_requirements_file(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        result = await mm._install_dependencies()
        assert result is True

    @pytest.mark.asyncio
    async def test_install_pip_failure(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "requirements.txt").write_text("nonexistent-package")

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("main.asyncio.create_subprocess_exec", return_value=mock_process):
            result = await mm._install_dependencies()

        assert result is False

    @pytest.mark.asyncio
    async def test_install_pip_not_found(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "requirements.txt").write_text("xgboost")

        with patch("main.asyncio.create_subprocess_exec", side_effect=FileNotFoundError("pip3 not found")):
            result = await mm._install_dependencies()

        assert result is False


class TestHasModel:
    """Tests for ModelManager.has_model()."""

    def test_has_model_true(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.has_model() is True

    def test_has_model_false_no_file(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.has_model() is False

    def test_has_model_false_no_dir(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "nonexistent"

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.has_model() is False


class TestRunPrediction:
    """Tests for ModelManager.run_prediction()."""

    @pytest.mark.asyncio
    async def test_successful_prediction(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")
        mm._last_prediction_wall_time = None

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"prediction result", b""))
        mock_process.kill = AsyncMock()
        mock_process.wait = AsyncMock()

        with patch("main.asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("main.MODEL_ENTRY_POINT", "predict.py"):
            result = await mm.run_prediction()

        assert result is True
        assert mm._last_prediction_wall_time is not None

    @pytest.mark.asyncio
    async def test_prediction_no_entry_point(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            result = await mm.run_prediction()

        assert result is False

    @pytest.mark.asyncio
    async def test_prediction_nonzero_exit(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("exit(1)")
        mm._last_prediction_wall_time = None

        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"error occurred"))
        mock_process.kill = AsyncMock()
        mock_process.wait = AsyncMock()

        with patch("main.asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("main.MODEL_ENTRY_POINT", "predict.py"):
            result = await mm.run_prediction()

        assert result is False
        assert mm._last_prediction_wall_time is None

    @pytest.mark.asyncio
    async def test_prediction_timeout(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("import time; time.sleep(999)")
        mm._last_prediction_wall_time = None

        mock_process = MagicMock()
        mock_process.communicate = MagicMock()  # Regular mock avoids unawaited coroutine warning
        mock_process.kill = MagicMock()  # kill() is synchronous on asyncio.subprocess.Process
        mock_process.wait = AsyncMock()

        with patch("main.asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_process)), \
             patch("main.MODEL_ENTRY_POINT", "predict.py"), \
             patch("main.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await mm.run_prediction()

        assert result is False

    @pytest.mark.asyncio
    async def test_prediction_os_error(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")

        with patch("main.asyncio.create_subprocess_exec", side_effect=OSError("No such file")), \
             patch("main.MODEL_ENTRY_POINT", "predict.py"):
            result = await mm.run_prediction()

        assert result is False

    @pytest.mark.asyncio
    async def test_prediction_updates_wall_time(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")
        mm._last_prediction_wall_time = None

        before = datetime.now(timezone.utc)

        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_process.kill = AsyncMock()
        mock_process.wait = AsyncMock()

        with patch("main.asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("main.MODEL_ENTRY_POINT", "predict.py"):
            await mm.run_prediction()

        after = datetime.now(timezone.utc)
        assert mm._last_prediction_wall_time is not None
        assert before <= mm._last_prediction_wall_time <= after


class TestShouldRunPrediction:
    """Tests for ModelManager.should_run_prediction()."""

    def test_empty_schedule_returns_false(self):
        mm = ModelManager.__new__(ModelManager)
        assert mm.should_run_prediction("") is False

    def test_no_model_returns_false(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 * * * *") is False

    def test_first_run_returns_true(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")
        mm._last_prediction_wall_time = None

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 * * * *") is True

    def test_not_yet_time_returns_false(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")

        # Last run was 5 minutes ago, schedule is every hour
        now = datetime(2024, 1, 15, 12, 5, 0, tzinfo=timezone.utc)
        mm._last_prediction_wall_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 * * * *", now=now) is False

    def test_past_due_returns_true(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")

        # Last run was 2 hours ago, schedule is every hour
        now = datetime(2024, 1, 15, 14, 5, 0, tzinfo=timezone.utc)
        mm._last_prediction_wall_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 * * * *", now=now) is True

    def test_invalid_cron_returns_false(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")
        mm._last_prediction_wall_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("invalid cron") is False

    def test_every_2_hours_schedule(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")

        # Last run at 12:00, now is 13:30. Next run at 14:00. Not yet.
        now = datetime(2024, 1, 15, 13, 30, 0, tzinfo=timezone.utc)
        mm._last_prediction_wall_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 */2 * * *", now=now) is False

        # Now is 14:05. Should run.
        now2 = datetime(2024, 1, 15, 14, 5, 0, tzinfo=timezone.utc)
        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 */2 * * *", now=now2) is True

    def test_daily_schedule(self, tmp_path):
        mm = ModelManager.__new__(ModelManager)
        mm.model_dir = tmp_path / "model"
        mm.model_dir.mkdir()
        (mm.model_dir / "predict.py").write_text("print('hello')")

        # Daily at midnight. Last run yesterday. Now is today 00:05.
        now = datetime(2024, 1, 16, 0, 5, 0, tzinfo=timezone.utc)
        mm._last_prediction_wall_time = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)

        with patch("main.MODEL_ENTRY_POINT", "predict.py"):
            assert mm.should_run_prediction("0 0 * * *", now=now) is True
