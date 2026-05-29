# PhotoSync Add-on Documentation

## Configuration

### Step 1: Generate a Koofr app-specific password

1. Log into [Koofr](https://app.koofr.net)
2. Navigate to **Preferences > Password > App Passwords**
3. Click **Generate New Password**
4. Name it something like "HomeAssistant"
5. Copy the generated password -- you will not be able to see it again

**Important:** Use an app-specific password, not your main Koofr login password. App passwords can be revoked individually without affecting your account.

### Step 2: Configure the add-on

Go to the **Configuration** tab of this add-on and fill in:

- **koofr_email**: Your Koofr account email address
- **koofr_password**: The app-specific password from Step 1
- **remote_path**: The folder path in Koofr to sync from (default: `/PhotoSync`). This should match where your phone uploads photos.
- **folder_name**: The folder name to create on each USB drive (default: `PhotoSync`)
- **notify_service**: (Optional) A Home Assistant notify entity for push notifications, e.g. `notify.mobile_app_my_iphone`. Leave empty to disable notifications.
- **batch_size_mb**: Maximum staging batch size in MB (default: 5000). Photos are downloaded to the internal SSD first, verified, then copied to the USB drive in batches of this size. Increase if you have plenty of SSD space; decrease if space is tight.
- **exclude_patterns**: File patterns to skip. The defaults exclude macOS/Windows junk files (`.DS_Store`, `Thumbs.db`, etc.). Add patterns if needed.

Click **Save** after making changes, then restart the add-on.

### Step 3: Prepare a USB drive

1. Format your USB drive as **exFAT** or **ext4** (recommended; NTFS is not reliably supported on HA OS)
2. Plug it into your Home Assistant machine
3. HA auto-mounts it under `/media/<drive-label>/`

The add-on will create a `PhotoSync/` folder (or whatever `folder_name` is set to) on the drive during the first sync.

## Using the Web UI

Open **PhotoSync** from the Home Assistant sidebar (camera icon).

The UI shows:
- **Connected drives**: All drives detected under `/media/` with their labels and mount status
- **Storage bar**: Used/free space on each drive
- **Sync Now button**: Triggers a sync from Koofr to the selected drive
- **Live progress**: Download speed, file count, batch progress, and current phase
- **Eject button**: Flushes writes so you can safely unplug the drive

## How Sync Works

Syncing uses a staged approach for data integrity:

1. **Scan**: Lists all files on Koofr and compares against the USB drive to find what's new or changed
2. **Download**: Downloads a batch of files to the internal SSD staging area (`/share/photosync-staging/`)
3. **Verify download**: Checks downloaded files against Koofr using checksums
4. **Copy to drive**: Copies verified files from SSD staging to the USB drive
5. **Verify copy**: Checks USB drive files against staging using MD5 checksums
6. **Repeat**: If there are more files than fit in one batch, repeats steps 2-5

Large syncs are automatically split into batches (configurable via `batch_size_mb`). Daily photo syncs (5-30 photos) typically fit in a single batch.

The staging area is always cleaned up, even if the sync fails or is cancelled.

## Folder Structure

Photos on the USB drive mirror the Koofr folder structure:

```
/media/<drive-label>/PhotoSync/
  2025/
    01/
      IMG_0001.HEIC
      IMG_0002.MOV
    02/
      IMG_0100.HEIC
  2026/
    05/
      IMG_0500.HEIC
```

This matches the `%YR/%mR` (year/month) layout that the PhotoSync iOS app uses when uploading to Koofr.

## Notifications

If `notify_service` is configured, you will receive push notifications when:
- A sync starts
- A sync completes successfully (includes file count)
- A sync fails (includes error message)

## Troubleshooting

### Drive not showing up in the UI

- Make sure the drive is plugged in and recognized by HA. Check **Settings > System > Hardware**.
- The drive must be mounted under `/media/`. HA OS auto-mounts USB drives, but if it does not appear, try unplugging and re-plugging.
- **exFAT and ext4** are the supported formats. NTFS is not reliably supported on HA OS.

### Sync fails immediately

- Verify your Koofr credentials in the Configuration tab. The most common cause is an expired or incorrect app-specific password.
- Check that `remote_path` matches an existing folder in your Koofr account (default: `/PhotoSync`).
- Make sure the add-on has been restarted after any configuration change.

### Sync runs but no files appear

- Confirm that photos actually exist in the Koofr folder at the path you configured. Log into Koofr's web UI to check.
- If files exist but are being skipped, check `exclude_patterns` -- make sure you haven't accidentally excluded your photo file types.

### rclone errors in the log

- **401 Unauthorized**: Your Koofr app password is wrong or has been revoked. Generate a new one.
- **directory not found**: The `remote_path` does not exist in Koofr. Create it, or update the config to match an existing path.
- **permission denied on /media/...**: The drive may be mounted read-only. Try reformatting it or check HA's hardware settings.

### Where to find logs

Go to the **Log** tab of this add-on in Home Assistant. The log shows rclone output including files transferred, errors, and transfer statistics. Increase log verbosity by checking the add-on's rclone flags if you need more detail.
