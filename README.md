# PhotoSync - Home Assistant Add-on

Sync photos from [Koofr](https://koofr.eu) cloud storage to USB drives connected to your Home Assistant machine. Plug in a drive, photos sync automatically, unplug when done.

## Why

Replace iCloud/Google Photos with infrastructure you control:

1. iPhone uploads photos to Koofr overnight (via [PhotoSync iOS app](https://www.photosync-app.com/) at 2am while charging)
2. This add-on pulls those photos from Koofr to USB HDDs plugged into your HA box
3. Result: cloud copy (Koofr) + local HDD copy, no subscription lock-in

```
  iPhone (PhotoSync)  --> Koofr <-- browser access
       2am upload           |
                      rclone copy (WebDAV)
                            |
                    Home Assistant (this add-on)
                         /         \
                    USB #1        USB #2
                     HDD           HDD
```

Photos stay as HEIC/MOV originals. Folder structure is preserved (`2025/06/IMG_1234.HEIC`).

## Features

- **Auto-sync on drive mount** — configure drive labels, plug in, sync starts automatically
- **Manual sync** — Sync Now button in the web UI for any drive
- **Post-sync verification** — `rclone check` confirms all files are present after download
- **Never deletes** — uses `rclone copy`, only adds or overwrites. Koofr is read-only source of truth
- **iPhone notifications** — push notification when sync completes or fails
- **Live progress** — download speed, file count, current file in the web UI
- **Pause / Resume / Cancel** per drive
- **Eject button** — flushes writes for safe drive removal

## Prerequisites

- Home Assistant OS or Supervised installation
- A [Koofr](https://koofr.eu) account with photos uploaded
- One or more USB drives formatted as **exFAT** or **ext4**
- (Optional) [PhotoSync iOS app](https://www.photosync-app.com/) for automatic iPhone uploads to Koofr

## Installation

1. In Home Assistant, go to **Settings > Add-ons > Add-on Store**
2. Click the three-dot menu (top right) > **Repositories**
3. Add: `https://github.com/aksareen/ha-addon-photosync`
4. Find "PhotoSync" in the store and click **Install**
5. Configure (see DOCS tab), then start

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `koofr_email` | (required) | Koofr account email |
| `koofr_password` | (required) | Koofr [app-specific password](https://app.koofr.net/app/admin/preferences/password) |
| `remote_path` | `/PhotoSync` | Koofr folder to sync from |
| `folder_name` | `PhotoSync` | Folder created on each USB drive |
| `notify_service` | (optional) | HA notify entity, e.g. `notify.iphone_my_device` |
| `auto_sync_drives` | `[]` | Drive labels that trigger auto-sync on mount |
| `exclude_patterns` | OS junk files | File patterns to skip |

## How It Works

1. **Scan** — rclone checks which files on Koofr are missing from the USB drive
2. **Download** — copies new files directly from Koofr to the USB drive via WebDAV
3. **Verify** — `rclone check --size-only` confirms all Koofr files are present on the drive
4. **Flush** — `sync` ensures all writes are committed before notification
5. **Notify** — push notification on completion or failure

Resume is automatic — if interrupted, re-running sync skips files that already exist with the correct size.

## License

MIT
