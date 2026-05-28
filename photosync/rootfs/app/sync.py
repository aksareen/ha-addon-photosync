import os
import re
import subprocess


def run_sync(drive_label, mount_path, folder_name, remote_path, exclude_patterns,
             on_progress=None, on_complete=None, on_error=None):
    dest = os.path.join(mount_path, folder_name)
    os.makedirs(dest, exist_ok=True)

    cmd = [
        "rclone", "copy",
        f"koofr:{remote_path}/",
        f"{dest}/",
        "--config", "/data/rclone.conf",
        "--progress",
        "--stats", "2s",
    ]

    for pattern in exclude_patterns:
        cmd.extend(["--exclude", pattern])

    print(f"[photosync] sync started for {drive_label}: {' '.join(cmd)}")

    all_lines = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            line = line.rstrip("\n")
            all_lines.append(line)
            if on_progress:
                on_progress(line)

        proc.wait()

        if proc.returncode != 0:
            error_msg = f"rclone exited with code {proc.returncode}"
            print(f"[photosync] sync failed for {drive_label}: {error_msg}")
            if on_error:
                on_error(error_msg)
            return

        stats = _parse_rclone_stats(all_lines)
        print(f"[photosync] sync complete for {drive_label}: {stats}")
        if on_complete:
            on_complete(stats)

    except Exception as e:
        error_msg = str(e)
        print(f"[photosync] sync exception for {drive_label}: {error_msg}")
        if on_error:
            on_error(error_msg)


def _parse_rclone_stats(lines):
    stats = {"files_transferred": 0, "bytes_transferred": 0, "errors": 0}
    for line in reversed(lines):
        files_match = re.search(r"Transferred:\s+(\d+)\s+/\s+\d+", line)
        if files_match:
            stats["files_transferred"] = int(files_match.group(1))

        bytes_match = re.search(r"Transferred:\s+([\d.]+)\s*(\w?Bytes)", line)
        if bytes_match:
            val = float(bytes_match.group(1))
            unit = bytes_match.group(2).lower()
            multipliers = {"bytes": 1, "kbytes": 1024, "mbytes": 1048576, "gbytes": 1073741824}
            stats["bytes_transferred"] = int(val * multipliers.get(unit, 1))

        errors_match = re.search(r"Errors:\s+(\d+)", line)
        if errors_match:
            stats["errors"] = int(errors_match.group(1))
            break
    return stats


def send_notification(message, title="PhotoSync", notify_service=None):
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    _post_ha_service(
        "persistent_notification/create",
        {"message": message, "title": title},
        headers,
    )

    if notify_service:
        parts = notify_service.split(".", 1)
        if len(parts) == 2:
            service_name = parts[1]
            _post_ha_service(
                f"notify/{service_name}",
                {"message": message, "title": title},
                headers,
            )


def _post_ha_service(service_path, payload, headers):
    import json
    import urllib.request

    url = f"http://supervisor/core/api/services/{service_path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[photosync] notification sent: {service_path} ({resp.status})")
    except Exception as e:
        print(f"[photosync] notification failed: {service_path} ({e})")
