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
- **exclude_patterns**: File patterns to skip. The defaults exclude macOS/Windows junk files (`.DS_Store`, `Thumbs.db`, etc.). Add patterns if needed.

Click **Save** after making changes, then restart the add-on.

### Step 3: Prepare a USB drive

1. Format your USB drive (ext4, exFAT, or NTFS all work)
2. Plug it into your Home Assistant machine
3. HA auto-mounts it under `/media/<drive-label>/`

The add-on will create a `PhotoSync/` folder (or whatever `folder_name` is set to) on the drive during the first sync.

## Using the Web UI

Open **PhotoSync** from the Home Assistant sidebar (camera icon).

The UI shows:
- **Connected drives**: All drives detected under `/media/` with their labels and mount status
- **Sync Now button**: Triggers a sync from Koofr to the selected drive(s)
- **Sync status**: Progress indicator while sync is running

To sync:
1. Make sure your USB drive is plugged in and appears in the list
2. Click **Sync Now**
3. Wait for the sync to complete -- photos are copied directly from Koofr to the drive

You can sync to multiple drives. Each sync runs in parallel.

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
- Some drive formats may not be supported. ext4 and exFAT are the most reliable with HA OS.

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
