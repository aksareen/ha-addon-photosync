import os
import subprocess


def get_drives(folder_names):
    """List USB drives under /media.

    folder_names: a single folder name (str) or a list of folder names that
    each configured sync pair expects on the drive. has_sync_folder is True
    only when every configured folder already exists.
    """
    if isinstance(folder_names, str):
        folder_names = [folder_names]
    folder_names = [f for f in folder_names if f]

    media_root = "/media"
    drives = []

    if not os.path.isdir(media_root):
        return drives

    try:
        media_stat = os.statvfs(media_root)
        media_total = media_stat.f_frsize * media_stat.f_blocks
    except OSError:
        media_total = 0

    for entry in sorted(os.listdir(media_root)):
        mount_path = os.path.join(media_root, entry)
        if not os.path.isdir(mount_path):
            continue

        try:
            stat = os.statvfs(mount_path)
        except OSError:
            continue

        total = stat.f_frsize * stat.f_blocks
        if total == 0 or total == media_total:
            continue

        free = stat.f_frsize * stat.f_bavail
        used = total - free

        present = sum(
            1 for fn in folder_names
            if os.path.isdir(os.path.join(mount_path, fn))
        )

        drives.append({
            "id": entry,
            "label": entry,
            "mount_path": mount_path,
            "has_sync_folder": len(folder_names) > 0 and present == len(folder_names),
            "folders_total": len(folder_names),
            "folders_present": present,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
        })

    return drives


def safe_eject(mount_path):
    subprocess.run(["sync"], timeout=60)
