import ctypes
import ctypes.util
import os
import re
import subprocess
import threading

_ejected_lock = threading.Lock()
_ejected_devices = set()


def _parse_proc_partitions():
    partitions = []
    try:
        with open("/proc/partitions") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 4 and re.match(r"sd[a-z]+\d+$", parts[3]):
                    partitions.append({
                        "name": parts[3],
                        "device": f"/dev/{parts[3]}",
                        "size_bytes": int(parts[2]) * 1024,
                    })
    except Exception:
        pass
    return partitions


def _get_system_disk():
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and (
                    parts[1] == "/" or "/data" in parts[1] or "/supervisor" in parts[1]
                ):
                    m = re.match(r"/dev/(sd[a-z]+)", parts[0])
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return "sda"


def _run_blkid(device):
    try:
        result = subprocess.run(
            ["blkid", device], capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        out = result.stdout.strip()
        info = {}
        for key in ("LABEL", "TYPE", "UUID"):
            m = re.search(rf'{key}="([^"]*)"', out)
            if m:
                info[key.lower()] = m.group(1)
        return info
    except Exception:
        return {}


def _get_mounts():
    mounts = {}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("/dev/sd"):
                    mounts[parts[0]] = parts[1]
    except Exception:
        pass
    return mounts


def _sanitize_label(label):
    s = re.sub(r"[^\w\s-]", "", label)
    s = re.sub(r"\s+", "_", s.strip())
    return s or "usb_drive"


def _syscall_mount(source, target, fstype, options=""):
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    ret = libc.mount(
        source.encode(), target.encode(), fstype.encode(),
        0, options.encode() if options else None,
    )
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))


def mount_device(device):
    info = _run_blkid(device)
    fs_type = info.get("type")
    label = info.get("label", os.path.basename(device))

    if not fs_type:
        raise RuntimeError(f"Cannot identify filesystem on {device}")

    safe_label = _sanitize_label(label)
    mount_point = f"/media/{safe_label}"
    os.makedirs(mount_point, exist_ok=True)

    mount_fs = "ntfs3" if fs_type == "ntfs" else fs_type
    mount_opts = "force" if fs_type == "ntfs" else ""

    try:
        _syscall_mount(device, mount_point, mount_fs, mount_opts)
    except OSError as e:
        try:
            os.rmdir(mount_point)
        except OSError:
            pass
        raise RuntimeError(f"mount {device} as {mount_fs}: {e}")

    with _ejected_lock:
        _ejected_devices.discard(device)

    print(f"[photosync] mounted {device} ({fs_type}) at {mount_point}")
    return mount_point


def eject_device(mount_point, device=None):
    subprocess.run(["sync"], timeout=60)

    result = subprocess.run(
        ["umount", mount_point], capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    try:
        os.rmdir(mount_point)
    except OSError:
        pass

    if device:
        with _ejected_lock:
            _ejected_devices.add(device)

    print(f"[photosync] ejected {mount_point}")


def scan_and_mount():
    system_disk = _get_system_disk()
    mounts = _get_mounts()
    current_devices = set()
    mounted = []

    for part in _parse_proc_partitions():
        disk = re.match(r"(sd[a-z]+)", part["name"]).group(1)
        if disk == system_disk:
            continue

        current_devices.add(part["device"])

        if part["device"] in mounts:
            continue

        with _ejected_lock:
            if part["device"] in _ejected_devices:
                continue

        info = _run_blkid(part["device"])
        if not info.get("type"):
            continue

        try:
            mp = mount_device(part["device"])
            mounted.append({"device": part["device"], "mount_path": mp})
        except RuntimeError as e:
            print(f"[photosync] auto-mount failed {part['device']}: {e}")

    with _ejected_lock:
        _ejected_devices.difference_update(_ejected_devices - current_devices)

    return mounted


def get_drives(folder_name):
    system_disk = _get_system_disk()
    mounts = _get_mounts()
    drives = []

    for part in _parse_proc_partitions():
        disk = re.match(r"(sd[a-z]+)", part["name"]).group(1)
        if disk == system_disk:
            continue

        device = part["device"]
        dev_id = part["name"]
        info = _run_blkid(device)
        label = info.get("label", dev_id)
        fs_type = info.get("type", "unknown")
        mount_path = mounts.get(device)

        if mount_path:
            try:
                stat = os.statvfs(mount_path)
                total = stat.f_frsize * stat.f_blocks
                free = stat.f_frsize * stat.f_bavail
            except OSError:
                total = free = 0

            drives.append({
                "id": dev_id,
                "label": label,
                "device": device,
                "mount_path": mount_path,
                "mounted": True,
                "fs_type": fs_type,
                "has_sync_folder": os.path.isdir(os.path.join(mount_path, folder_name)),
                "total_bytes": total,
                "used_bytes": total - free,
                "free_bytes": free,
            })
        else:
            drives.append({
                "id": dev_id,
                "label": label,
                "device": device,
                "mount_path": None,
                "mounted": False,
                "fs_type": fs_type,
                "has_sync_folder": False,
                "total_bytes": part["size_bytes"],
                "used_bytes": 0,
                "free_bytes": part["size_bytes"],
            })

    return drives
