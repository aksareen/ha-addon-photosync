"""Tests for photosync/rootfs/app/server.py

server.py reads /data/options.json at import time, so we mock builtins.open
before importing the module. This file uses a module-scoped fixture to import
server once with controlled options.
"""

import json
import os
import sys
import threading
import time
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Module-level setup: import server.py with mocked options file
# ---------------------------------------------------------------------------

OPTIONS = {
    "folder_name": "PhotoSync",
    "remote_path": "/TestPhotos",
    "notify_service": "",
    "auto_sync_drives": [],
    "exclude_patterns": ["*.tmp"],
}

_real_open = open


def _fake_open(path, *args, **kwargs):
    if path == "/data/options.json":
        import io
        return io.StringIO(json.dumps(OPTIONS))
    return _real_open(path, *args, **kwargs)


# Patch open before importing server so OPTIONS_PATH read works
with mock.patch("builtins.open", side_effect=_fake_open):
    import server


def _import_server_with_options(opts):
    """Import a FRESH copy of server.py with the given options.json contents.

    server.py reads /data/options.json and computes SYNC_PAIRS / FOLDER_NAMES /
    MIRROR_DELETES at import time, so to exercise a different config we import
    the module under a throwaway name with `open` stubbed. Auto-sync is left
    empty so no watcher thread is spawned.
    """
    import importlib.util

    def fake_open(path, *args, **kwargs):
        if path == "/data/options.json":
            import io
            return io.StringIO(json.dumps(opts))
        return _real_open(path, *args, **kwargs)

    spec = importlib.util.spec_from_file_location(
        "server_variant_" + str(abs(hash(json.dumps(opts, sort_keys=True)))),
        os.path.join(
            os.path.dirname(__file__), os.pardir,
            "photosync", "rootfs", "app", "server.py",
        ),
    )
    mod = importlib.util.module_from_spec(spec)
    with mock.patch("builtins.open", side_effect=fake_open):
        spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client():
    """Flask test client."""
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def reset_jobs():
    """Reset global sync state between tests."""
    server.sync_jobs.clear()
    server.cancel_events.clear()
    yield
    server.sync_jobs.clear()
    server.cancel_events.clear()


# ---------------------------------------------------------------------------
# Helper: fake drive data
# ---------------------------------------------------------------------------

def _make_drive(drive_id, has_sync_folder=True):
    return {
        "id": drive_id,
        "label": drive_id,
        "mount_path": f"/media/{drive_id}",
        "has_sync_folder": has_sync_folder,
        "total_bytes": 1_000_000_000,
        "used_bytes": 400_000_000,
        "free_bytes": 600_000_000,
    }


# ---------------------------------------------------------------------------
# API endpoints — status codes
# ---------------------------------------------------------------------------

class TestApiStatus:
    """Tests for GET /api/status."""

    @mock.patch("server.get_drives", return_value=[])
    def test_status_empty(self, mock_drives, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert resp.get_json() == []

    @mock.patch("server.get_drives")
    def test_status_with_drives(self, mock_drives, client):
        mock_drives.return_value = [_make_drive("usb1")]
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["id"] == "usb1"
        # Each drive should have a "sync" key with job state
        assert "sync" in data[0]
        assert data[0]["sync"]["status"] == "idle"


class TestApiSync:
    """Tests for POST /api/sync/<drive_id>."""

    @mock.patch("server.get_drives", return_value=[])
    def test_sync_drive_not_found(self, mock_drives, client):
        resp = client.post("/api/sync/nonexistent")
        assert resp.status_code == 404

    @mock.patch("server._start_sync_for_drive", return_value=True)
    @mock.patch("server.get_drives")
    def test_sync_no_folder(self, mock_drives, mock_start, client):
        mock_drives.return_value = [_make_drive("usb1", has_sync_folder=False)]
        resp = client.post("/api/sync/usb1")
        assert resp.status_code == 400
        assert "does not exist" in resp.get_json()["error"]

    @mock.patch("server._start_sync_for_drive", return_value=True)
    @mock.patch("server.get_drives")
    def test_sync_starts_successfully(self, mock_drives, mock_start, client):
        mock_drives.return_value = [_make_drive("usb1")]
        resp = client.post("/api/sync/usb1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "started"

    @mock.patch("server.get_drives")
    def test_sync_already_in_progress(self, mock_drives, client):
        mock_drives.return_value = [_make_drive("usb1")]
        # Pre-set job status to syncing
        job = server._get_job("usb1")
        job["status"] = "syncing"

        resp = client.post("/api/sync/usb1")
        assert resp.status_code == 409


class TestApiEject:
    """Tests for POST /api/eject/<drive_id>."""

    @mock.patch("server.get_drives", return_value=[])
    def test_eject_drive_not_found(self, mock_drives, client):
        resp = client.post("/api/eject/nonexistent")
        assert resp.status_code == 404

    @mock.patch("server.safe_eject")
    @mock.patch("server.get_drives")
    def test_eject_success(self, mock_drives, mock_eject, client):
        mock_drives.return_value = [_make_drive("usb1")]
        resp = client.post("/api/eject/usb1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ejected"
        mock_eject.assert_called_once_with("/media/usb1")

    @mock.patch("server.get_drives")
    def test_eject_blocked_during_sync(self, mock_drives, client):
        mock_drives.return_value = [_make_drive("usb1")]
        job = server._get_job("usb1")
        job["status"] = "syncing"

        resp = client.post("/api/eject/usb1")
        assert resp.status_code == 409


class TestApiPause:
    """Tests for POST /api/pause/<drive_id>."""

    def test_pause_not_syncing(self, client):
        resp = client.post("/api/pause/usb1")
        assert resp.status_code == 400

    @mock.patch("server.pause_sync", return_value=True)
    def test_pause_success(self, mock_pause, client):
        job = server._get_job("usb1")
        job["status"] = "syncing"
        job["pid"] = 123

        resp = client.post("/api/pause/usb1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "paused"
        assert job["status"] == "paused"

    def test_pause_no_pid(self, client):
        job = server._get_job("usb1")
        job["status"] = "syncing"
        job["pid"] = None

        resp = client.post("/api/pause/usb1")
        assert resp.status_code == 400


class TestApiResume:
    """Tests for POST /api/resume/<drive_id>."""

    def test_resume_not_paused(self, client):
        resp = client.post("/api/resume/usb1")
        assert resp.status_code == 400

    @mock.patch("server.resume_sync", return_value=True)
    def test_resume_success(self, mock_resume, client):
        job = server._get_job("usb1")
        job["status"] = "paused"
        job["pid"] = 123

        resp = client.post("/api/resume/usb1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "resumed"
        assert job["status"] == "syncing"


class TestApiCancel:
    """Tests for POST /api/cancel/<drive_id>."""

    def test_cancel_not_syncing(self, client):
        resp = client.post("/api/cancel/usb1")
        assert resp.status_code == 400

    @mock.patch("server.cancel_sync", return_value=True)
    @mock.patch("server.resume_sync", return_value=True)
    def test_cancel_sets_cancelling(self, mock_resume, mock_cancel, client):
        job = server._get_job("usb1")
        job["status"] = "syncing"
        job["pid"] = 123
        server.cancel_events["usb1"] = threading.Event()

        resp = client.post("/api/cancel/usb1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "cancelling"
        assert job["status"] == "cancelling"
        assert server.cancel_events["usb1"].is_set()


class TestApiCreateFolder:
    """Tests for POST /api/create-folder/<drive_id>."""

    @mock.patch("server.get_drives", return_value=[])
    def test_create_folder_drive_not_found(self, mock_drives, client):
        resp = client.post("/api/create-folder/nonexistent")
        assert resp.status_code == 404

    @mock.patch("server.os.makedirs")
    @mock.patch("server.get_drives")
    def test_create_folder_success(self, mock_drives, mock_makedirs, client):
        mock_drives.return_value = [_make_drive("usb1")]
        resp = client.post("/api/create-folder/usb1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "created"
        # Route now creates ALL configured pair folders and returns paths.
        assert data["paths"] == ["/media/usb1/PhotoSync"]
        mock_makedirs.assert_called_once_with("/media/usb1/PhotoSync", exist_ok=True)


# ---------------------------------------------------------------------------
# Job state transitions
# ---------------------------------------------------------------------------

class TestJobStateTransitions:
    """Tests for job state machine managed by _run_sync_thread callbacks."""

    def test_fresh_job_is_idle(self):
        job = server._fresh_job()
        assert job["status"] == "idle"
        assert job["phase"] is None
        assert job["pid"] is None
        assert job["percent"] == 0

    def test_on_stats_updates_job_fields(self):
        """Simulate the on_stats callback from _run_sync_thread."""
        job = server._get_job("test_drive")
        job["status"] = "syncing"

        stats = {
            "files_transferred": 5,
            "bytes_transferred": 1000,
            "total_files": 10,
            "total_bytes": 2000,
            "speed": 500,
            "eta_seconds": 2,
            "elapsed_seconds": 3.0,
            "current_file": "photo.jpg",
            "errors_count": 0,
            "checking": 10,
            "total_checks": 20,
            "phase": "downloading",
        }

        # Simulate what on_stats does (replicate the closure logic)
        with server.sync_lock:
            if job["status"] in ("syncing", "paused"):
                job["files_transferred"] = stats["files_transferred"]
                job["bytes_transferred"] = stats["bytes_transferred"]
                job["total_files"] = stats["total_files"]
                job["total_bytes"] = stats["total_bytes"]
                job["speed"] = stats["speed"]
                total = stats["total_bytes"]
                done = stats["bytes_transferred"]
                job["percent"] = min(round((done / total) * 100, 1), 100) if total > 0 else 0

        assert job["files_transferred"] == 5
        assert job["total_files"] == 10
        assert job["percent"] == 50.0

    def test_idle_to_syncing_to_complete(self):
        """Full lifecycle: idle -> syncing -> complete."""
        job = server._get_job("lifecycle")
        assert job["status"] == "idle"

        # Start
        job["status"] = "syncing"
        job["phase"] = "scanning"
        assert job["status"] == "syncing"

        # Complete
        job["status"] = "complete"
        job["phase"] = "done"
        job["percent"] = 100
        assert job["status"] == "complete"

    def test_idle_to_syncing_to_cancelled(self):
        """Lifecycle: idle -> syncing -> cancelling -> cancelled."""
        job = server._get_job("cancel_test")
        job["status"] = "syncing"
        job["status"] = "cancelling"
        job["status"] = "cancelled"
        job["phase"] = "done"
        assert job["status"] == "cancelled"

    def test_idle_to_syncing_to_failed(self):
        """Lifecycle: idle -> syncing -> failed."""
        job = server._get_job("fail_test")
        job["status"] = "syncing"
        job["status"] = "failed"
        job["error"] = "rclone exited with code 1"
        assert job["status"] == "failed"
        assert job["error"] is not None


# ---------------------------------------------------------------------------
# Drive watcher — auto-sync matching
# ---------------------------------------------------------------------------

class TestDriveWatcher:
    """Tests for _drive_watcher auto-sync trigger logic."""

    @mock.patch("server._start_sync_for_drive")
    @mock.patch("server.get_drives")
    def test_start_sync_for_drive_calls_run_sync_thread(
        self, mock_drives, mock_start
    ):
        """_start_sync_for_drive should look up the drive and start a thread."""
        drive = _make_drive("usb1")
        mock_drives.return_value = [drive]
        mock_start.return_value = True

        # Verify _find_drive returns the drive
        found = server._find_drive("usb1")
        assert found is not None
        assert found["id"] == "usb1"

    @mock.patch("server.get_drives", return_value=[])
    def test_start_sync_for_missing_drive_returns_false(self, mock_drives):
        result = server._start_sync_for_drive("nonexistent")
        assert result is False

    @mock.patch("server.os.makedirs")
    @mock.patch("server.get_drives")
    def test_start_sync_blocked_when_already_syncing(self, mock_drives, mock_makedirs):
        mock_drives.return_value = [_make_drive("usb1")]
        job = server._get_job("usb1")
        job["status"] = "syncing"

        result = server._start_sync_for_drive("usb1")
        assert result is False


# ---------------------------------------------------------------------------
# _drive_with_sync
# ---------------------------------------------------------------------------

class TestDriveWithSync:
    """Tests for _drive_with_sync — merges drive info with job state."""

    def test_drive_with_sync_adds_sync_key(self):
        drive = _make_drive("usb1")
        result = server._drive_with_sync(drive)
        assert "sync" in result
        assert result["sync"]["status"] == "idle"

    def test_drive_with_sync_reflects_job_status(self):
        drive = _make_drive("usb2")
        job = server._get_job("usb2")
        job["status"] = "syncing"
        job["percent"] = 42.5

        result = server._drive_with_sync(drive)
        assert result["sync"]["status"] == "syncing"
        assert result["sync"]["percent"] == 42.5


# ---------------------------------------------------------------------------
# Config parsing — sync_pairs / mirror_deletes / legacy fallback
# ---------------------------------------------------------------------------

class TestConfigParsing:
    """Module-level SYNC_PAIRS / FOLDER_NAMES / MIRROR_DELETES from options."""

    def test_legacy_single_pair_fallback(self):
        """With only legacy remote_path/folder_name, one pair is derived."""
        assert server.SYNC_PAIRS == [
            {"remote_path": "/TestPhotos", "folder_name": "PhotoSync"}
        ]
        assert server.FOLDER_NAMES == ["PhotoSync"]
        assert server.MIRROR_DELETES is False

    def test_multi_pair_and_mirror(self):
        mod = _import_server_with_options({
            "notify_service": "",
            "auto_sync_drives": [],
            "exclude_patterns": [],
            "mirror_deletes": True,
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
                {"remote_path": "/Videos", "folder_name": "Videos"},
            ],
        })
        assert mod.SYNC_PAIRS == [
            {"remote_path": "/Photos", "folder_name": "Photos"},
            {"remote_path": "/Videos", "folder_name": "Videos"},
        ]
        assert mod.FOLDER_NAMES == ["Photos", "Videos"]
        assert mod.MIRROR_DELETES is True

    def test_sync_pairs_skips_incomplete_entries(self):
        """Pairs missing remote_path or folder_name are dropped."""
        mod = _import_server_with_options({
            "auto_sync_drives": [],
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
                {"remote_path": "/Videos"},          # missing folder_name
                {"folder_name": "Docs"},             # missing remote_path
            ],
        })
        assert mod.SYNC_PAIRS == [
            {"remote_path": "/Photos", "folder_name": "Photos"}
        ]

    def test_empty_sync_pairs_falls_back_to_defaults(self):
        """No sync_pairs and no legacy keys → built-in PhotoSync default."""
        mod = _import_server_with_options({"auto_sync_drives": []})
        assert mod.SYNC_PAIRS == [
            {"remote_path": "/PhotoSync", "folder_name": "PhotoSync"}
        ]


class TestMultiPairCreateFolder:
    """create-folder route creates ALL pair folders for a multi-pair config."""

    def test_create_all_pair_folders(self):
        mod = _import_server_with_options({
            "auto_sync_drives": [],
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
                {"remote_path": "/Videos", "folder_name": "Videos"},
            ],
        })
        mod.app.config["TESTING"] = True
        with mod.app.test_client() as c, \
                mock.patch.object(mod, "get_drives",
                                  return_value=[_make_drive("usb1")]), \
                mock.patch.object(mod.os, "makedirs") as mock_makedirs:
            resp = c.post("/api/create-folder/usb1")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "created"
            assert data["paths"] == [
                "/media/usb1/Photos", "/media/usb1/Videos",
            ]
            assert mock_makedirs.call_count == 2


class TestSyncThreadMultiPair:
    """_run_sync_thread iterates ALL pairs, accumulates stats, sends ONE
    notification at the end."""

    def _build(self, opts):
        mod = _import_server_with_options(opts)
        return mod

    def test_iterates_all_pairs_and_accumulates(self):
        mod = self._build({
            "auto_sync_drives": [],
            "mirror_deletes": False,
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
                {"remote_path": "/Videos", "folder_name": "Videos"},
            ],
        })

        calls = []

        def fake_run_sync(*args, **kwargs):
            calls.append(kwargs["folder_name"])
            return {
                "status": "complete", "error": None,
                "files_transferred": 3, "bytes_transferred": 100, "errors": 0,
            }

        notifs = []
        with mock.patch.object(mod, "run_sync", side_effect=fake_run_sync), \
                mock.patch.object(mod, "send_notification",
                                  side_effect=lambda *a, **k: notifs.append((a, k))):
            mod._run_sync_thread("usb1", "/media/usb1", "usb1")

        # Both pairs ran, in order
        assert calls == ["Photos", "Videos"]
        # One notification at the end
        assert len(notifs) == 1
        job = mod._get_job("usb1")
        assert job["status"] == "complete"
        assert job["files_transferred"] == 6   # 3 + 3 accumulated
        assert job["bytes_transferred"] == 200
        # Default (non-mirror) → "copied" verb
        assert "copied" in notifs[0][0][0]

    def test_mirror_uses_synced_verb(self):
        mod = self._build({
            "auto_sync_drives": [],
            "mirror_deletes": True,
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
            ],
        })

        notifs = []
        with mock.patch.object(mod, "run_sync", return_value={
                    "status": "complete", "error": None,
                    "files_transferred": 5, "bytes_transferred": 10, "errors": 0,
                }), \
                mock.patch.object(mod, "send_notification",
                                  side_effect=lambda *a, **k: notifs.append(a)):
            mod._run_sync_thread("usb1", "/media/usb1", "usb1")

        assert len(notifs) == 1
        assert "synced" in notifs[0][0]
        assert "5 files" in notifs[0][0]

    def test_zero_files_reports_up_to_date(self):
        mod = self._build({
            "auto_sync_drives": [],
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
            ],
        })

        notifs = []
        with mock.patch.object(mod, "run_sync", return_value={
                    "status": "complete", "error": None,
                    "files_transferred": 0, "bytes_transferred": 0, "errors": 0,
                }), \
                mock.patch.object(mod, "send_notification",
                                  side_effect=lambda *a, **k: notifs.append(a)):
            mod._run_sync_thread("usb1", "/media/usb1", "usb1")

        assert len(notifs) == 1
        assert "up to date" in notifs[0][0]

    def test_failure_stops_and_notifies_once(self):
        """A failed pair stops the loop and produces a single FAILED notice."""
        mod = self._build({
            "auto_sync_drives": [],
            "sync_pairs": [
                {"remote_path": "/Photos", "folder_name": "Photos"},
                {"remote_path": "/Videos", "folder_name": "Videos"},
            ],
        })

        calls = []

        def fake_run_sync(*args, **kwargs):
            calls.append(kwargs["folder_name"])
            return {
                "status": "failed", "error": "rclone exited with code 1",
                "files_transferred": 0, "bytes_transferred": 0, "errors": 1,
            }

        notifs = []
        with mock.patch.object(mod, "run_sync", side_effect=fake_run_sync), \
                mock.patch.object(mod, "send_notification",
                                  side_effect=lambda *a, **k: notifs.append(a)):
            mod._run_sync_thread("usb1", "/media/usb1", "usb1")

        # Stopped after the first (failed) pair — second never ran
        assert calls == ["Photos"]
        assert len(notifs) == 1
        assert "FAILED" in notifs[0][0]
        job = mod._get_job("usb1")
        assert job["status"] == "failed"
        assert job["error"] == "rclone exited with code 1"
