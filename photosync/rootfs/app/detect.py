import os


def get_drives(folder_name):
    media_root = "/media"
    drives = []

    if not os.path.isdir(media_root):
        return drives

    for entry in sorted(os.listdir(media_root)):
        mount_path = os.path.join(media_root, entry)
        if not os.path.isdir(mount_path):
            continue

        try:
            stat = os.statvfs(mount_path)
        except OSError:
            continue

        total = stat.f_frsize * stat.f_blocks
        free = stat.f_frsize * stat.f_bavail
        used = total - free

        sync_folder = os.path.join(mount_path, folder_name)
        has_sync_folder = os.path.isdir(sync_folder)

        drives.append({
            "label": entry,
            "mount_path": mount_path,
            "has_sync_folder": has_sync_folder,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
        })

    return drives
