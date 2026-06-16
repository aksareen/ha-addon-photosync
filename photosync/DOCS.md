# PhotoSync Add-on Documentation

## Configuration

### Step 1: Generate a Koofr app-specific password

1. Log into [Koofr](https://app.koofr.net)
2. Navigate to **Preferences > Password > App Passwords**
3. Click **Generate New Password**
4. Name it something like "HomeAssistant"
5. Copy the generated password -- you will not be able to see it again

**Important:** Use an app-specific password, not your main Koofr login password.

### Step 2: Configure the add-on

Go to the **Configuration** tab and fill in:

- **koofr_email**: Your Koofr account email address
- **koofr_password**: The app-specific password from Step 1
- **sync_pairs**: A list of `{remote_path, folder_name}` entries — each maps one Koofr folder (`remote_path`) to one folder created on each USB drive (`folder_name`). See [Sync Pairs](#sync-pairs) below. Leave empty to use the single built-in default pair (`/PhotoSync` → `PhotoSync`).
- **mirror_deletes**: (Optional, default `false`) When `false`, sync is add-only (`rclone copy`). When `true`, the drive is mirrored to match Koofr (`rclone sync`), so files removed from Koofr are also removed from the drive. See [Mirror Mode](#mirror-mode) below.
- **notify_service**: (Optional) A Home Assistant notify entity for push notifications, e.g. `notify.iphone_my_device`. Leave empty to disable.
- **auto_sync_drives**: (Optional) List of drive labels that trigger auto-sync when plugged in. Also syncs matching drives on add-on startup. Example: `["PhotoSync", "Backup"]`
- **exclude_patterns**: File patterns to skip. The defaults exclude macOS/Windows junk files.

Click **Save** after making changes, then restart the add-on.

### Sync Pairs

All syncing is configured through `sync_pairs` — a list where each entry maps one Koofr folder onto one folder on the drive:

```yaml
sync_pairs:
  - remote_path: "/PhotoSync"
    folder_name: "PhotoSync"
  - remote_path: "/AllPhotos"
    folder_name: "AllPhotos"
```

Each entry copies one Koofr folder (`remote_path`) into a folder of that name (`folder_name`) on the USB drive. All configured folders are created together by the **Create Folder** button and are synced one after another. While a sync runs, the web UI shows progress across pairs, e.g. `(2/3 · AllPhotos)`.

If `sync_pairs` is left empty, the add-on uses a single built-in default pair (`/PhotoSync` → `PhotoSync`) so it still runs out of the box.

Both `remote_path` and `folder_name` may contain spaces — for example `"/All photos and videos"`. Quote such values in YAML. Spaces are preserved verbatim and work correctly because rclone is invoked with an argument list rather than through a shell.

### Mirror Mode

`mirror_deletes` controls whether deletions on Koofr are propagated to the drive:

- **`false` (default)**: The add-on uses `rclone copy` — add-only. New files are added or overwritten, and nothing is ever deleted from the drive.
- **`true`**: The add-on uses `rclone sync` — the drive is made to **match** Koofr. Files that were deleted, moved, or reorganized on Koofr are also removed from the drive. This avoids stale duplicates and reclaims space.

When mirror mode is on, **Koofr is the source of truth and the drive is a downstream mirror.** This is destructive but recoverable: if a file is wrongly removed from the drive, you can recover it by re-syncing from Koofr. There is intentionally no on-drive trash or backup folder. File comparison is size-only, because Koofr's WebDAV interface exposes no modification time or hash.

When mirror mode is enabled, the web UI shows a "Mirror mode — deletes propagate" indicator in the header.

### Step 3: Prepare a USB drive

1. Format your USB drive as **exFAT** or **ext4** (NTFS is not supported on HA OS)
2. Plug it into your Home Assistant machine
3. HA auto-mounts it under `/media/<drive-label>/`

## Using the Web UI

Open **PhotoSync** from the Home Assistant sidebar (camera icon).

- **Connected drives** with storage bar (used/free space)
- **Sync Now** — manually trigger a sync from Koofr to the drive (runs all configured sync pairs in sequence)
- **Create Folder** — creates the sync folder(s) on a new drive (one per configured sync pair)
- **Pause / Resume / Cancel** — control a running sync
- **Eject** — flushes writes so you can safely unplug
- **Refresh Drives** — re-scan for connected drives
- **Transfer log** — expandable rclone output

## How Sync Works

For each configured sync pair, in sequence:

1. rclone scans the Koofr folder and the matching folder on the USB drive to find new or changed files
2. New files are downloaded directly from Koofr to the USB drive
3. In mirror mode (`mirror_deletes: true`), files that no longer exist on Koofr are removed from the drive
4. `rclone check --size-only` verifies all Koofr files are present on the drive
5. Writes are flushed to disk

Resume is automatic: if sync is interrupted, re-running it skips files that already exist with the correct size.

By default (`mirror_deletes: false`) the add-on uses `rclone copy` (not `sync`), so it **never deletes** files from the drive. With `mirror_deletes: true` it uses `rclone sync` and the drive is made to match Koofr — see [Mirror Mode](#mirror-mode). In either case the add-on never writes to Koofr; Koofr remains the source of truth.

## Auto-Sync

Add drive labels to `auto_sync_drives` in the configuration. When a matching drive is plugged in (or already connected at add-on startup), sync starts automatically. You'll receive a notification when it finishes.

## Notifications

If `notify_service` is configured, you receive push notifications when:
- Sync completes (with file count, or "up to date")
- Sync fails (with error message)

## Folder Structure

Photos on the USB drive mirror the Koofr folder structure:

```
/media/<drive-label>/<folder_name>/
  2025/
    01/
      IMG_0001.HEIC
      IMG_0002.MOV
  2026/
    05/
      IMG_0500.HEIC
```

## Troubleshooting

### Drive not showing up

- Check **Settings > System > Hardware** to confirm HA sees the drive
- Try unplugging and re-plugging
- Only **exFAT** and **ext4** are supported

### Sync fails with 401

- Your Koofr app password is wrong or expired. Generate a new one at Koofr > Preferences > Password > App Passwords

### Sync fails with "directory not found"

- A `remote_path` in `sync_pairs` doesn't exist in Koofr. Create it, or update the config.

### Where to find logs

Go to the **Log** tab of this add-on. Shows rclone output including files transferred and errors.
