# PhotoSync - Home Assistant Add-on

Sync photos from [Koofr](https://koofr.eu) cloud storage to USB drives connected to your Home Assistant machine. Downloads are staged on the internal SSD with checksum verification before writing to the USB drive — protects against download corruption and USB write errors.

## Why

Replace iCloud/Google Photos with infrastructure you control:

1. iPhone uploads photos to Koofr overnight (via [PhotoSync iOS app](https://www.photosync-app.com/) at 2am while charging)
2. This add-on pulls those photos from Koofr to USB HDDs plugged into your HA box
3. Result: cloud copy (Koofr) + local HDD copy, no subscription lock-in

```
                          +-----------+
  iPhone (PhotoSync)  --> |   Koofr   | <-- browser access
       2am upload         +-----+-----+
                                |
                          rclone copy
                                |
                      +---------+---------+
                      |   Home Assistant  |
                      |   (this add-on)   |
                      |                   |
                      |  SSD staging area |
                      |  /share/photosync |
                      |  -staging/        |
                      +---------+---------+
                           /         \
                     +----+---+  +---+----+
                     | USB #1 |  | USB #2 |
                     | HDD    |  | HDD    |
                     +--------+  +--------+
```

Photos stay as HEIC/MOV originals. Folder structure is preserved (`/PhotoSync/2025/06/`, etc.).

## Prerequisites

- Home Assistant OS or Supervised installation
- A [Koofr](https://koofr.eu) account
- One or more USB drives formatted as **exFAT** or **ext4** (NTFS is not reliably supported on HA OS)
- (Optional) [PhotoSync iOS app](https://www.photosync-app.com/) configured to upload to Koofr

## Installation

1. In Home Assistant, go to **Settings > Add-ons > Add-on Store**
2. Click the three-dot menu (top right) and select **Repositories**
3. Add this repository URL:
   ```
   https://github.com/aksareen/ha-addon-photosync
   ```
4. Find "PhotoSync" in the store and click **Install**
5. Configure the add-on (see below), then start it

## Configuration

Set these in the add-on's **Configuration** tab:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `koofr_email` | email | (required) | Your Koofr account email |
| `koofr_password` | password | (required) | Koofr **app-specific password** (not your main password -- see below) |
| `remote_path` | string | `/PhotoSync` | Path in Koofr to sync from |
| `folder_name` | string | `PhotoSync` | Folder name created on each USB drive |
| `notify_service` | string | (optional) | HA notification entity, e.g. `notify.mobile_app_my_iphone` |
| `batch_size_mb` | int | `5000` | Max staging batch size in MB. Downloads are split into batches of this size. |
| `exclude_patterns` | list | `.DS_Store`, `Thumbs.db`, etc. | File patterns to skip during sync |

### Koofr app-specific password

Do **not** use your main Koofr password. Generate an app-specific one:

1. Log into [Koofr](https://app.koofr.net)
2. Go to **Preferences > Password > App Passwords**
3. Click **Generate New Password**
4. Give it a name (e.g. "HomeAssistant") and copy the generated password
5. Paste it into the `koofr_password` field in the add-on config

### How rclone connects

The add-on generates `rclone.conf` at runtime from your config options. No manual rclone configuration needed. It uses WebDAV to connect to Koofr.

## Usage

1. Plug a USB drive into your HA machine
2. Open **PhotoSync** in the HA sidebar (camera icon)
3. The web UI shows connected drives under `/media/`
4. Click **Create Folder** if needed, then **Sync Now**

### How sync works

Syncing uses a staged approach for data integrity:

1. **Scan** — lists all files on Koofr, compares against the USB drive to find what's new or changed
2. **Download** — downloads a batch to the internal SSD staging area (`/share/photosync-staging/`)
3. **Verify download** — checks staged files against Koofr (size verification)
4. **Copy to drive** — copies verified files from SSD staging to the USB drive
5. **Verify copy** — checks USB drive files against staging using MD5 checksums
6. **Repeat** — if more files remain, repeats steps 2-5 with the next batch

Large syncs are automatically split into batches (configurable via `batch_size_mb`). The staging area is always cleaned up, even on failure or cancellation.

The add-on uses `rclone copy` (not `sync`), so it **never deletes** files from the destination or from Koofr. Koofr is treated as a read-only source of truth.

### Web UI features

- Live progress: download speed, file count, batch progress, current phase
- Pause / Resume / Cancel sync per drive
- Eject button: flushes writes for safe drive removal
- Refresh button: re-scan for drives
- Transfer log: expandable rclone output

### Notifications

If `notify_service` is set, the add-on sends push notifications on:
- Sync started
- Sync completed (with file count and checksum status)
- Sync failed (with error details)

Persistent notifications also appear in the HA notification panel.

## Roadmap

- [x] v0.1: Manual sync via web UI
- [x] v0.2: Live progress with pause/resume/cancel
- [x] v0.4: Simplified for exFAT (no privileged mounting)
- [x] v0.5: SSD staging with batched downloads and checksum verification
- [ ] Auto-trigger sync when USB drive is mounted (with debounce)
- [ ] Cross-HDD sync (copy between drives locally for redundancy)
- [ ] Koofr storage usage monitoring (alert at 90%)

## License

MIT
