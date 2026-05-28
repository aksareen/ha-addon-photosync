# CONFIG USB for HAOS NTFS Drive Mounting

Copy the contents of this directory to a FAT32-formatted USB stick
with partition label `CONFIG`.

## Steps

1. Format a USB stick as FAT32, set partition label to `CONFIG`
2. Copy these files to the root of the USB stick preserving structure:
   ```
   cp -r modules/ udev/ authorized_keys /media/<your-usb>/
   ```
3. Plug CONFIG USB into the HA box
4. From SSH add-on: `ha os import` (or reboot with it plugged in)
5. Remove CONFIG USB
6. Plug in Seagate drive — should auto-mount to `/media/Seagate Backup Plus Drive/`

## What each file does

- `modules/ntfs3.conf` — loads the ntfs3 kernel module at boot
- `udev/80-mount-usb-to-media-by-label.rules` — auto-mounts USB drives to `/media/<label>/`
- `authorized_keys` — enables emergency debug SSH on port 22222 (optional)

## Important

- All files MUST use Unix (LF) line endings
- Persists across HAOS updates — one-time setup
