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
- **remote_path**: The folder path in Koofr to sync from (default: `/PhotoSync`). Should match where your phone uploads photos.
- **folder_name**: The folder name to create on each USB drive (default: `PhotoSync`). Tip: if your drive label is also "PhotoSync", change this to something like "SyncedPhotos" to avoid a nested `PhotoSync/PhotoSync/` path.
- **notify_service**: (Optional) A Home Assistant notify entity for push notifications, e.g. `notify.iphone_my_device`. Leave empty to disable.
- **auto_sync_drives**: (Optional) List of drive labels that trigger auto-sync when plugged in. Also syncs matching drives on add-on startup. Example: `["PhotoSync", "Backup"]`
- **exclude_patterns**: File patterns to skip. The defaults exclude macOS/Windows junk files.

Click **Save** after making changes, then restart the add-on.

### Step 3: Prepare a USB drive

1. Format your USB drive as **exFAT** or **ext4** (NTFS is not supported on HA OS)
2. Plug it into your Home Assistant machine
3. HA auto-mounts it under `/media/<drive-label>/`

## Using the Web UI

Open **PhotoSync** from the Home Assistant sidebar (camera icon).

- **Connected drives** with storage bar (used/free space)
- **Sync Now** — manually trigger a sync from Koofr to the drive
- **Create Folder** — creates the sync folder on a new drive
- **Pause / Resume / Cancel** — control a running sync
- **Eject** — flushes writes so you can safely unplug
- **Refresh Drives** — re-scan for connected drives
- **Transfer log** — expandable rclone output

## How Sync Works

1. rclone scans Koofr and the USB drive to find new or changed files
2. New files are downloaded directly from Koofr to the USB drive
3. After download, `rclone check --size-only` verifies all Koofr files are present on the drive
4. Writes are flushed to disk

Resume is automatic: if sync is interrupted, re-running it skips files that already exist with the correct size. The add-on uses `rclone copy` (not `sync`), so it **never deletes** files from the drive or from Koofr.

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

- The `remote_path` doesn't exist in Koofr. Create it, or update the config.

### Where to find logs

Go to the **Log** tab of this add-on. Shows rclone output including files transferred and errors.
