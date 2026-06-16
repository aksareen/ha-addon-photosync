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
- **Multiple sync pairs** — sync several Koofr folders to differently-named folders on the same drive in one run
- **Copy or mirror** — add-only by default (`rclone copy`), or opt into mirror mode (`rclone sync`) to keep the drive matching Koofr
- **Post-sync verification** — `rclone check` confirms all files are present after download
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
| `sync_pairs` | `[]` | List of `{remote_path, folder_name}` entries — sync multiple Koofr folders in one run. See below |
| `remote_path` | `/PhotoSync` | Legacy single-pair fallback: Koofr folder to sync from when `sync_pairs` is empty |
| `folder_name` | `PhotoSync` | Legacy single-pair fallback: folder created on each USB drive when `sync_pairs` is empty |
| `mirror_deletes` | `false` | When `true`, use `rclone sync` so the drive matches Koofr (deletes propagate). See below |
| `notify_service` | (optional) | HA notify entity, e.g. `notify.iphone_my_device` |
| `auto_sync_drives` | `[]` | Drive labels that trigger auto-sync on mount |
| `exclude_patterns` | OS junk files | File patterns to skip |

### Multiple sync pairs

Set `sync_pairs` to copy more than one Koofr folder onto a drive, each into its own destination folder:

```yaml
sync_pairs:
  - remote_path: "/PhotoSync"
    folder_name: "PhotoSync"
  - remote_path: "/AllPhotos"
    folder_name: "AllPhotos"
```

Each entry maps one Koofr folder (`remote_path`) to a folder of name `folder_name` on the USB drive. All configured folders are created together by the **Create Folder** button and synced one after another; the UI shows progress across pairs like `(2/3 · AllPhotos)`.

If `sync_pairs` is left empty, the add-on falls back to the legacy single `remote_path` + `folder_name` options (defaults `/PhotoSync` and `PhotoSync`), so existing configs keep working unchanged.

### Mirror mode (`mirror_deletes`)

- `false` (default): `rclone copy` — add-only, never deletes. New files are added or overwritten; nothing on the drive is removed.
- `true`: `rclone sync` — the drive is made to **match** Koofr. Files deleted or moved/reorganized on Koofr are also removed from the drive, so there are no stale duplicates and space is reclaimed.

In mirror mode **Koofr is the source of truth and the drive is a downstream mirror**. This is destructive but recoverable: if a file is wrongly removed from the drive, re-sync from Koofr to bring it back. There is intentionally no on-drive trash or backup. Comparison is size-only, since Koofr's WebDAV exposes no modification time or hash. When enabled, the web UI shows a "Mirror mode — deletes propagate" indicator in the header.

## How It Works

For each configured sync pair (or the single legacy folder if `sync_pairs` is empty), the add-on runs the following in sequence:

1. **Scan** — rclone compares the Koofr folder against the matching folder on the USB drive
2. **Transfer** — by default (`mirror_deletes: false`) `rclone copy` adds new files; in mirror mode (`mirror_deletes: true`) `rclone sync` also removes files from the drive that no longer exist on Koofr
3. **Verify** — `rclone check --size-only` confirms all Koofr files are present on the drive
4. **Flush** — `sync` ensures all writes are committed before notification
5. **Notify** — push notification on completion or failure

Resume is automatic — if interrupted, re-running sync skips files that already exist with the correct size.

## License

MIT
