"""Tests for photosync/rootfs/app/detect.py"""

import os
from unittest import mock

import pytest

import detect


# ---------------------------------------------------------------------------
# get_drives
# ---------------------------------------------------------------------------

class TestGetDrives:
    """Tests for get_drives — USB drive detection via /media."""

    @mock.patch("detect.os.path.isdir")
    def test_returns_empty_when_media_not_a_dir(self, mock_isdir):
        mock_isdir.return_value = False
        result = detect.get_drives("PhotoSync")
        assert result == []

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=[])
    @mock.patch("detect.os.statvfs")
    def test_returns_empty_when_no_entries(self, mock_statvfs, mock_listdir, mock_isdir):
        mock_isdir.return_value = True
        mock_statvfs.return_value = mock.MagicMock(
            f_frsize=4096, f_blocks=1000, f_bavail=500
        )
        result = detect.get_drives("PhotoSync")
        assert result == []

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["usb_drive_1", "usb_drive_2"])
    @mock.patch("detect.os.statvfs")
    def test_filters_out_same_filesystem_as_media(
        self, mock_statvfs, mock_listdir, mock_isdir
    ):
        """Drives whose total_bytes match /media's total are filtered out
        (they are bind-mounts of the root filesystem, not USB drives)."""
        mock_isdir.return_value = True

        # /media itself: 4096 * 1000 = 4096000
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)
        # usb_drive_1: same total as /media — should be filtered out
        same_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=200)
        # usb_drive_2: different total — should be included
        diff_stat = mock.MagicMock(f_frsize=4096, f_blocks=5000, f_bavail=3000)

        def statvfs_side_effect(path):
            if path == "/media":
                return media_stat
            if path == "/media/usb_drive_1":
                return same_stat
            if path == "/media/usb_drive_2":
                return diff_stat
            raise OSError("unexpected path")

        mock_statvfs.side_effect = statvfs_side_effect

        result = detect.get_drives("PhotoSync")
        assert len(result) == 1
        assert result[0]["id"] == "usb_drive_2"
        assert result[0]["label"] == "usb_drive_2"
        assert result[0]["mount_path"] == "/media/usb_drive_2"
        assert result[0]["total_bytes"] == 4096 * 5000
        assert result[0]["free_bytes"] == 4096 * 3000
        assert result[0]["used_bytes"] == 4096 * (5000 - 3000)

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["zero_drive"])
    @mock.patch("detect.os.statvfs")
    def test_filters_out_zero_size_drives(self, mock_statvfs, mock_listdir, mock_isdir):
        """Drives reporting total_bytes == 0 are filtered out."""
        mock_isdir.return_value = True
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)
        zero_stat = mock.MagicMock(f_frsize=4096, f_blocks=0, f_bavail=0)

        def statvfs_side_effect(path):
            if path == "/media":
                return media_stat
            return zero_stat

        mock_statvfs.side_effect = statvfs_side_effect

        result = detect.get_drives("PhotoSync")
        assert result == []

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["usb1"])
    @mock.patch("detect.os.statvfs")
    def test_has_sync_folder_true(self, mock_statvfs, mock_listdir, mock_isdir):
        """has_sync_folder is True when the sync folder exists on the drive."""
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)
        drive_stat = mock.MagicMock(f_frsize=4096, f_blocks=2000, f_bavail=1000)

        mock_statvfs.side_effect = lambda path: (
            media_stat if path == "/media" else drive_stat
        )

        def isdir_side_effect(path):
            if path == "/media":
                return True
            if path == "/media/usb1":
                return True
            if path == "/media/usb1/PhotoSync":
                return True
            return False

        mock_isdir.side_effect = isdir_side_effect

        result = detect.get_drives("PhotoSync")
        assert len(result) == 1
        assert result[0]["has_sync_folder"] is True

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["usb1"])
    @mock.patch("detect.os.statvfs")
    def test_has_sync_folder_false(self, mock_statvfs, mock_listdir, mock_isdir):
        """has_sync_folder is False when the sync folder does not exist."""
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)
        drive_stat = mock.MagicMock(f_frsize=4096, f_blocks=2000, f_bavail=1000)

        mock_statvfs.side_effect = lambda path: (
            media_stat if path == "/media" else drive_stat
        )

        def isdir_side_effect(path):
            if path == "/media":
                return True
            if path == "/media/usb1":
                return True
            # /media/usb1/PhotoSync does NOT exist
            return False

        mock_isdir.side_effect = isdir_side_effect

        result = detect.get_drives("PhotoSync")
        assert len(result) == 1
        assert result[0]["has_sync_folder"] is False

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["file_not_dir"])
    @mock.patch("detect.os.statvfs")
    def test_skips_non_directory_entries(self, mock_statvfs, mock_listdir, mock_isdir):
        """Entries under /media that are not directories are skipped."""
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)
        mock_statvfs.return_value = media_stat

        def isdir_side_effect(path):
            if path == "/media":
                return True
            return False  # file_not_dir is not a directory

        mock_isdir.side_effect = isdir_side_effect

        result = detect.get_drives("PhotoSync")
        assert result == []

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["oserr_drive"])
    @mock.patch("detect.os.statvfs")
    def test_skips_drives_with_statvfs_error(
        self, mock_statvfs, mock_listdir, mock_isdir
    ):
        """Drives that raise OSError on statvfs are skipped gracefully."""
        mock_isdir.return_value = True
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)

        def statvfs_side_effect(path):
            if path == "/media":
                return media_stat
            raise OSError("device not ready")

        mock_statvfs.side_effect = statvfs_side_effect

        result = detect.get_drives("PhotoSync")
        assert result == []

    @mock.patch("detect.os.path.isdir")
    @mock.patch("detect.os.listdir", return_value=["b_drive", "a_drive"])
    @mock.patch("detect.os.statvfs")
    def test_drives_returned_in_sorted_order(
        self, mock_statvfs, mock_listdir, mock_isdir
    ):
        """Drives should be sorted alphabetically by entry name."""
        mock_isdir.return_value = True
        media_stat = mock.MagicMock(f_frsize=4096, f_blocks=1000, f_bavail=500)
        drive_stat = mock.MagicMock(f_frsize=4096, f_blocks=2000, f_bavail=1000)

        mock_statvfs.side_effect = lambda path: (
            media_stat if path == "/media" else drive_stat
        )

        result = detect.get_drives("PhotoSync")
        assert len(result) == 2
        assert result[0]["id"] == "a_drive"
        assert result[1]["id"] == "b_drive"


# ---------------------------------------------------------------------------
# safe_eject
# ---------------------------------------------------------------------------

class TestSafeEject:
    """Tests for safe_eject — flushes filesystem buffers."""

    @mock.patch("detect.subprocess.run")
    def test_safe_eject_calls_sync(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)
        detect.safe_eject("/media/usb1")
        mock_run.assert_called_once_with(["sync"], timeout=60)
