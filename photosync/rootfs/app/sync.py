import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request

RCLONE_CONFIG = "/data/rclone.conf"


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _rc_call(port, endpoint):
    url = f"http://127.0.0.1:{port}/{endpoint}"
    req = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _wait_for_rc(port, proc, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        if _rc_call(port, "core/version"):
            return True
        time.sleep(0.3)
    return False


def _read_lines(stream, callback):
    try:
        for line in stream:
            stripped = line.rstrip("\n")
            if stripped and callback:
                callback(stripped)
    except Exception:
        pass


def run_sync(drive_label, mount_path, folder_name, remote_path, exclude_patterns,
             on_start=None, on_progress=None, on_stats=None,
             on_complete=None, on_error=None):
    dest = os.path.join(mount_path, folder_name)
    os.makedirs(dest, exist_ok=True)

    rc_port = _find_free_port()
    src = f"koofr:{remote_path}/"

    cmd = [
        "rclone", "copy", src, f"{dest}/",
        "--config", RCLONE_CONFIG,
        "--rc", f"--rc-addr=127.0.0.1:{rc_port}", "--rc-no-auth",
        "--stats", "0",
        "-v",
        "--check-first",
    ]
    for pat in exclude_patterns:
        cmd.extend(["--exclude", pat])

    print(f"[photosync] sync {drive_label}: {' '.join(cmd)}")

    proc = None
    last_stats = {}

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        if on_start:
            on_start(proc.pid)

        reader = threading.Thread(
            target=_read_lines, args=(proc.stdout, on_progress), daemon=True,
        )
        reader.start()

        rc_ok = _wait_for_rc(rc_port, proc)

        if rc_ok:
            while proc.poll() is None:
                raw = _rc_call(rc_port, "core/stats")
                if raw:
                    xferring = raw.get("transferring") or []
                    current = xferring[0].get("name", "") if xferring else ""
                    last_stats = {
                        "bytes_transferred": raw.get("bytes", 0),
                        "total_bytes": raw.get("totalBytes", 0),
                        "files_transferred": raw.get("transfers", 0),
                        "total_files": raw.get("totalTransfers", 0),
                        "speed": raw.get("speed", 0),
                        "eta_seconds": raw.get("eta"),
                        "elapsed_seconds": raw.get("elapsedTime", 0),
                        "errors_count": raw.get("errors", 0),
                        "current_file": current,
                        "checking": raw.get("checks", 0),
                        "total_checks": raw.get("totalChecks", 0),
                    }
                    if on_stats:
                        on_stats(last_stats)
                time.sleep(1)

        proc.wait()
        reader.join(timeout=5)

        if proc.returncode != 0:
            if on_error:
                on_error(f"rclone exited with code {proc.returncode}")
            return

        if on_complete:
            on_complete({
                "files_transferred": last_stats.get("files_transferred", 0),
                "bytes_transferred": last_stats.get("bytes_transferred", 0),
                "errors": last_stats.get("errors_count", 0),
            })

    except Exception as e:
        print(f"[photosync] exception: {e}")
        if on_error:
            on_error(str(e))
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


def pause_sync(pid):
    try:
        os.kill(pid, signal.SIGSTOP)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def resume_sync(pid):
    try:
        os.kill(pid, signal.SIGCONT)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def cancel_sync(pid):
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError):
        return False


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
            _post_ha_service(
                f"notify/{parts[1]}",
                {"message": message, "title": title},
                headers,
            )


def _post_ha_service(service_path, payload, headers):
    url = f"http://supervisor/core/api/services/{service_path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[photosync] notified: {service_path} ({resp.status})")
    except Exception as e:
        print(f"[photosync] notify failed: {service_path} ({e})")
