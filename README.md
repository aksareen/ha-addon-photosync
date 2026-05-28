# PhotoSync - Home Assistant Add-on

Sync photos from [Koofr](https://koofr.eu) cloud storage directly to USB hard drives connected to your Home Assistant machine. No intermediate SSD buffer, no format conversion -- just `rclone copy` from cloud to HDD.

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
- One or more USB drives, formatted and mounted (HA mounts them under `/media/<label>/`)
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
| `notify_service` | string | (optional) | HA notification entity, e.g. `notify.iphone_ash_16_device` |
| `exclude_patterns` | list | `.DS_Store`, `Thumbs.db`, etc. | File patterns to skip during sync |

### Koofr app-specific password

Do **not** use your main Koofr password. Generate an app-specific one:

1. Log into [Koofr](https://app.koofr.net)
2. Go to **Preferences > Password > App Passwords**
3. Click **Generate New Password**
4. Give it a name (e.g. "HomeAssistant") and copy the generated password
5. Paste it into the `koofr_password` field in the add-on config

### How rclone connects

The add-on generates `rclone.conf` at runtime from your config options. No manual rclone configuration needed. It uses the native Koofr backend (not WebDAV).

## Usage

1. Plug a USB drive into your HA machine
2. Open **PhotoSync** in the HA sidebar (camera icon)
3. The web UI shows connected drives under `/media/`
4. Click **Sync Now** to pull photos from Koofr to the drive

Sync copies files to `/media/<drive-label>/PhotoSync/` preserving the year/month subfolder structure from Koofr (e.g. `2025/06/IMG_1234.HEIC`).

Multiple drives can sync in parallel. The add-on uses `rclone copy` (not `sync`), so it **never deletes** files from the destination.

### Notifications

If `notify_service` is set, the add-on sends HA notifications on:
- Sync started
- Sync completed (with file count)
- Sync failed (with error details)

## How It Works

- **No SSD buffer**: rclone streams directly from Koofr to the USB HDD
- **No conversion**: HEIC photos and MOV videos are kept as-is
- **Additive only**: `rclone copy` adds new files, never removes existing ones
- **Ingress UI**: the web panel runs on port 8099 behind HA's ingress proxy, no port forwarding needed
- **HA API access**: used for sending notifications and detecting mounted media

## Roadmap

- [x] v0.1: Manual sync via web UI
- [ ] v0.2: Auto-trigger sync when USB drive is mounted (with debounce)
- [ ] v0.3: Cross-HDD sync (copy between drives for redundancy)
- [ ] v0.4: Storage monitoring dashboard (drive capacity, last sync time, file counts)

## License

MIT
