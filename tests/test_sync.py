"""Tests for photosync/rootfs/app/sync.py"""

import json
import os
import subprocess
import threading
from unittest import mock

import pytest

import sync


# ---------------------------------------------------------------------------
# _verify_sync
# ---------------------------------------------------------------------------

class TestVerifySync:
    """Tests for _verify_sync — rclone check wrapper."""

    @mock.patch("sync.subprocess.run")
    def test_verify_sync_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        # Should not raise
        sync._verify_sync("/Photos", "/mnt/usb/PhotoSync", ["*.tmp"])
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0:2] == ["rclone", "check"]
        assert "koofr:/Photos/" in cmd
        assert "/mnt/usb/PhotoSync/" in cmd
        assert "--one-way" in cmd
        assert "--size-only" in cmd
        assert "--config" in cmd
        assert "--exclude" in cmd
        assert "*.tmp" in cmd

    @mock.patch("sync.subprocess.run")
    def test_verify_sync_failure_raises(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="3 differences found"
        )
        with pytest.raises(RuntimeError, match="Post-sync verification failed"):
            sync._verify_sync("/Photos", "/mnt/usb/PhotoSync", [])

    @mock.patch("sync.subprocess.run")
    def test_verify_sync_failure_falls_back_to_stdout(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="mismatch info", stderr=""
        )
        with pytest.raises(RuntimeError, match="mismatch info"):
            sync._verify_sync("/Photos", "/mnt/usb/PhotoSync", [])

    @mock.patch("sync.subprocess.run")
    def test_verify_sync_failure_unknown(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        with pytest.raises(RuntimeError, match="unknown"):
            sync._verify_sync("/Photos", "/mnt/usb/PhotoSync", [])

    @mock.patch("sync.subprocess.run")
    def test_verify_sync_multiple_excludes(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        sync._verify_sync("/Photos", "/mnt/usb/PhotoSync", ["*.tmp", "Thumbs.db"])
        cmd = mock_run.call_args[0][0]
        # There should be two --exclude flags
        exclude_indices = [i for i, x in enumerate(cmd) if x == "--exclude"]
        assert len(exclude_indices) == 2
        assert cmd[exclude_indices[0] + 1] == "*.tmp"
        assert cmd[exclude_indices[1] + 1] == "Thumbs.db"

    @mock.patch("sync.subprocess.run")
    def test_verify_sync_timeout(self, mock_run):
        _, kwargs = None, {}
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        sync._verify_sync("/Photos", "/dest", [])
        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] == 600


# ---------------------------------------------------------------------------
# send_notification / _call_ha_service
# ---------------------------------------------------------------------------

class TestNotification:
    """Tests for send_notification and _call_ha_service."""

    @mock.patch("sync.subprocess.run")
    def test_call_ha_service_curl_args(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="200", stderr=""
        )
        sync._call_ha_service(
            "persistent_notification/create",
            {"message": "hello", "title": "PhotoSync"},
            "test-token-abc",
        )
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "curl"
        assert "-X" in cmd
        assert "POST" in cmd
        # Check URL
        url = cmd[-1]
        assert url == "http://supervisor/core/api/services/persistent_notification/create"
        # Check auth header
        auth_idx = cmd.index("-H")
        assert "Bearer test-token-abc" in cmd[auth_idx + 1]
        # Check payload
        d_idx = cmd.index("-d")
        payload = json.loads(cmd[d_idx + 1])
        assert payload["message"] == "hello"

    @mock.patch("sync._call_ha_service")
    def test_send_notification_persistent_only(self, mock_call):
        os.environ["SUPERVISOR_TOKEN"] = "tok123"
        try:
            sync.send_notification("test msg", title="Test")
            # Only persistent_notification should be called when no notify_service
            assert mock_call.call_count == 1
            args = mock_call.call_args_list[0]
            assert args[0][0] == "persistent_notification/create"
        finally:
            os.environ.pop("SUPERVISOR_TOKEN", None)

    @mock.patch("sync._call_ha_service")
    def test_send_notification_with_notify_service(self, mock_call):
        os.environ["SUPERVISOR_TOKEN"] = "tok123"
        try:
            sync.send_notification(
                "test msg", title="Test", notify_service="notify.mobile"
            )
            assert mock_call.call_count == 2
            first_call = mock_call.call_args_list[0]
            second_call = mock_call.call_args_list[1]
            assert first_call[0][0] == "persistent_notification/create"
            assert second_call[0][0] == "notify/send_message"
            assert second_call[0][1]["entity_id"] == "notify.mobile"
        finally:
            os.environ.pop("SUPERVISOR_TOKEN", None)


# ---------------------------------------------------------------------------
# run_sync — callback sequence
# ---------------------------------------------------------------------------

class TestRunSync:
    """Tests for run_sync — the main sync orchestrator.

    run_sync no longer takes on_complete/on_error callbacks; instead it
    RETURNS a result dict:
        {"status", "error", "files_transferred", "bytes_transferred", "errors"}
    """

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_success_returns_complete(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """When rclone succeeds, on_start fires with the pid and the returned
        dict reports status == 'complete' with the result keys present."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        # subprocess.run is called for the final `sync` command
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        started = {}

        def on_start(pid):
            started["pid"] = pid

        result = sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=[],
            on_start=on_start,
        )

        assert started["pid"] == 42
        assert result["status"] == "complete"
        assert result["error"] is None
        assert "files_transferred" in result
        assert "bytes_transferred" in result
        assert "errors" in result
        mock_verify.assert_called_once()

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_rclone_failure_returns_failed(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """When rclone exits non-zero, the returned dict reports failure and the
        error string, and verification is skipped."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 1
        proc.returncode = 1
        proc.stdout = iter([])
        proc.wait.return_value = 1
        mock_popen.return_value = proc

        result = sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=[],
        )

        assert result["status"] == "failed"
        assert "exited with code 1" in result["error"]
        mock_verify.assert_not_called()

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_verification_failure_returns_failed(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """A post-sync verification failure surfaces as status == 'failed'."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        mock_verify.side_effect = RuntimeError("Post-sync verification failed: 3 differences found")

        result = sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=[],
        )

        assert result["status"] == "failed"
        assert "verification failed" in result["error"].lower()

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_cancel_event_returns_cancelled(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """A pre-set cancel_event yields status == 'cancelled' (not complete or
        failed), no verification, and a cancel message on on_progress."""
        cancel = threading.Event()
        cancel.set()  # pre-cancelled

        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        progress = []

        result = sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=[],
            cancel_event=cancel,
            on_progress=lambda l: progress.append(l),
        )

        assert result["status"] == "cancelled"
        mock_verify.assert_not_called()
        # The cancel message should be in progress
        assert any("cancelled" in p.lower() for p in progress)

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_mirror_builds_rclone_sync_command(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """mirror=True → `rclone sync ... --size-only`, with no copy-only or
        backup/delete-guard flags."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=[],
            mirror=True,
        )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "rclone"
        assert cmd[1] == "sync"
        assert "--size-only" in cmd
        # mirror must not add a copy command or any backup/delete guards
        assert "copy" not in cmd
        assert "--backup-dir" not in cmd
        assert "--max-delete" not in cmd
        assert "--suffix" not in cmd

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_copy_builds_rclone_copy_command(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """mirror=False (default) → `rclone copy ...` (add-only, no deletes)."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=[],
            mirror=False,
        )

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "rclone"
        assert cmd[1] == "copy"
        assert "sync" not in cmd

    @mock.patch("sync._verify_sync")
    @mock.patch("sync._wait_for_rc", return_value=False)
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_run_sync_exclude_patterns_passed_through(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_wait_rc, mock_verify
    ):
        """exclude_patterns are forwarded to the rclone command as --exclude."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.poll.return_value = 0
        proc.returncode = 0
        proc.stdout = iter([])
        proc.wait.return_value = 0
        mock_popen.return_value = proc
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        sync.run_sync(
            drive_label="USB1",
            mount_path="/mnt/usb",
            folder_name="PhotoSync",
            remote_path="/Photos",
            exclude_patterns=["*.tmp", "Thumbs.db"],
        )

        cmd = mock_popen.call_args[0][0]
        exclude_indices = [i for i, x in enumerate(cmd) if x == "--exclude"]
        assert len(exclude_indices) == 2
        assert cmd[exclude_indices[0] + 1] == "*.tmp"
        assert cmd[exclude_indices[1] + 1] == "Thumbs.db"


# ---------------------------------------------------------------------------
# Stats latching
# ---------------------------------------------------------------------------

class TestStatsLatching:
    """Tests for the latched_total_files/latched_total_bytes logic in run_sync."""

    @mock.patch("sync._verify_sync")
    @mock.patch("sync.subprocess.run")
    @mock.patch("sync.subprocess.Popen")
    @mock.patch("sync._find_free_port", return_value=9999)
    @mock.patch("sync.os.makedirs")
    def test_stats_latching_on_transferring(
        self, mock_makedirs, mock_port, mock_popen, mock_run, mock_verify
    ):
        """When transferring list is non-empty, totals are latched and phase is 'downloading'."""
        proc = mock.MagicMock()
        proc.pid = 42
        proc.stdout = iter([])
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        poll_count = [0]

        def poll_side_effect():
            poll_count[0] += 1
            if poll_count[0] <= 2:
                return None  # still running
            return 0  # done

        proc.poll.side_effect = poll_side_effect

        # Mock _wait_for_rc and _rc_call
        stats_calls = []

        rc_responses = [
            # First call: scanning (no transferring)
            {
                "transferring": [],
                "transfers": 0,
                "totalTransfers": 100,
                "totalBytes": 5000,
                "bytes": 0,
                "speed": 0,
                "eta": None,
                "errors": 0,
                "checks": 50,
                "totalChecks": 100,
            },
            # Second call: downloading (has transferring)
            {
                "transferring": [{"name": "photo1.jpg"}],
                "transfers": 1,
                "totalTransfers": 42,
                "totalBytes": 2000,
                "bytes": 500,
                "speed": 100,
                "eta": 15,
                "errors": 0,
                "checks": 100,
                "totalChecks": 100,
            },
        ]
        rc_call_count = [0]

        def mock_rc_call(port, endpoint):
            if endpoint == "core/version":
                return {"version": "test"}
            if endpoint == "core/stats":
                idx = min(rc_call_count[0], len(rc_responses) - 1)
                rc_call_count[0] += 1
                return rc_responses[idx]
            return None

        def on_stats(s):
            stats_calls.append(dict(s))

        with mock.patch("sync._rc_call", side_effect=mock_rc_call), \
             mock.patch("sync._wait_for_rc", return_value=True), \
             mock.patch("sync.time.sleep"):
            sync.run_sync(
                drive_label="USB1",
                mount_path="/mnt/usb",
                folder_name="PhotoSync",
                remote_path="/Photos",
                exclude_patterns=[],
                on_stats=on_stats,
            )

        # First stats call should be scanning phase
        assert stats_calls[0]["phase"] == "scanning"

        # Second stats call: transferring list is non-empty, phase should be "downloading"
        assert stats_calls[1]["phase"] == "downloading"
        # Total files should be latched to the value from the second RC response
        assert stats_calls[1]["total_files"] == 42
        assert stats_calls[1]["total_bytes"] == 2000


# ---------------------------------------------------------------------------
# pause / resume / cancel helpers
# ---------------------------------------------------------------------------

class TestProcessControl:
    """Tests for pause_sync, resume_sync, cancel_sync."""

    @mock.patch("sync.os.kill")
    def test_pause_sync_sends_sigstop(self, mock_kill):
        result = sync.pause_sync(123)
        mock_kill.assert_called_once_with(123, sync.signal.SIGSTOP)
        assert result is True

    @mock.patch("sync.os.kill", side_effect=ProcessLookupError)
    def test_pause_sync_missing_process(self, mock_kill):
        result = sync.pause_sync(999)
        assert result is False

    @mock.patch("sync.os.kill")
    def test_resume_sync_sends_sigcont(self, mock_kill):
        result = sync.resume_sync(123)
        mock_kill.assert_called_once_with(123, sync.signal.SIGCONT)
        assert result is True

    @mock.patch("sync.os.kill")
    def test_cancel_sync_sends_sigterm(self, mock_kill):
        result = sync.cancel_sync(123)
        mock_kill.assert_called_once_with(123, sync.signal.SIGTERM)
        assert result is True


# ---------------------------------------------------------------------------
# _read_lines
# ---------------------------------------------------------------------------

class TestReadLines:
    """Tests for the _read_lines helper."""

    def test_read_lines_calls_callback(self):
        lines = ["line 1\n", "line 2\n", "\n", "line 3\n"]
        collected = []
        sync._read_lines(iter(lines), collected.append)
        # Empty lines (after strip) are skipped
        assert collected == ["line 1", "line 2", "line 3"]

    def test_read_lines_none_callback(self):
        lines = ["line 1\n"]
        # Should not raise
        sync._read_lines(iter(lines), None)
