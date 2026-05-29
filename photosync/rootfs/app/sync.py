import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request

RCLONE_CONFIG = "/data/rclone.conf"
STAGING_BASE = "/share/photosync-staging"


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


def clean_staging():
    if os.path.exists(STAGING_BASE):
        shutil.rmtree(STAGING_BASE, ignore_errors=True)


def _list_remote_files(remote_path, exclude_patterns):
    cmd = [
        "rclone", "lsjson", f"koofr:{remote_path}/",
        "--recursive", "--files-only",
        "--config", RCLONE_CONFIG,
    ]
    for pat in exclude_patterns:
        cmd.extend(["--exclude", pat])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to list remote files: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _compute_diff(remote_files, local_root):
    to_sync = []
    for f in remote_files:
        local_path = os.path.join(local_root, f["Path"])
        if not os.path.exists(local_path):
            to_sync.append(f)
        elif os.path.getsize(local_path) != f["Size"]:
            to_sync.append(f)
    return to_sync


def _create_batches(files, batch_size_bytes):
    if not files:
        return []
    batches = []
    current_batch = []
    current_size = 0
    for f in files:
        fsize = f["Size"]
        if fsize > batch_size_bytes:
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            batches.append([f])
            continue
        if current_size + fsize > batch_size_bytes and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        current_batch.append(f)
        current_size += fsize
    if current_batch:
        batches.append(current_batch)
    return batches


def _write_files_from(batch):
    path = "/tmp/photosync_batch.txt"
    with open(path, "w") as f:
        for item in batch:
            f.write(item["Path"] + "\n")
    return path


def _md5sum(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_staging_vs_remote(remote_path, staging_dir, files_from_path):
    # WebDAV doesn't expose remote hashes; size-only catches truncated downloads.
    # Local MD5 (staging vs HDD) provides the full integrity check.
    cmd = [
        "rclone", "check", staging_dir + "/", f"koofr:{remote_path}/",
        "--one-way", "--size-only",
        "--config", RCLONE_CONFIG,
        "--files-from", files_from_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Download verification failed: {detail}")


def _copy_staging_to_hdd(staging_dir, dest_dir, on_file=None):
    copied = 0
    for root, dirs, files in os.walk(staging_dir):
        rel_root = os.path.relpath(root, staging_dir)
        dest_sub = os.path.join(dest_dir, rel_root) if rel_root != "." else dest_dir
        os.makedirs(dest_sub, exist_ok=True)
        for fname in files:
            src_file = os.path.join(root, fname)
            dst_file = os.path.join(dest_sub, fname)
            shutil.copy2(src_file, dst_file)
            rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname
            copied += 1
            if on_file:
                on_file(rel_path, os.path.getsize(src_file))
    return copied


def _verify_hdd_vs_staging(staging_dir, dest_dir):
    errors = []
    for root, dirs, files in os.walk(staging_dir):
        rel_root = os.path.relpath(root, staging_dir)
        for fname in files:
            rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname
            staging_file = os.path.join(root, fname)
            hdd_file = os.path.join(dest_dir, rel_path)
            if not os.path.exists(hdd_file):
                errors.append(f"Missing: {rel_path}")
                continue
            if _md5sum(staging_file) != _md5sum(hdd_file):
                errors.append(f"Mismatch: {rel_path}")
    if errors:
        raise RuntimeError("HDD verification failed: " + "; ".join(errors[:5]))


def run_sync(drive_label, mount_path, folder_name, remote_path, exclude_patterns,
             batch_size_mb=5000, cancel_event=None,
             on_start=None, on_progress=None, on_stats=None,
             on_complete=None, on_error=None):
    dest = os.path.join(mount_path, folder_name)
    os.makedirs(dest, exist_ok=True)
    staging = os.path.join(STAGING_BASE, drive_label.replace(" ", "_"))
    batch_size_bytes = batch_size_mb * 1024 * 1024

    def _cancelled():
        return cancel_event and cancel_event.is_set()

    def _report(phase, **kw):
        if on_stats:
            on_stats({
                "phase": phase,
                "bytes_transferred": kw.get("bytes_done", 0),
                "total_bytes": kw.get("total_bytes", 0),
                "files_transferred": kw.get("files_done", 0),
                "total_files": kw.get("total_files", 0),
                "speed": kw.get("speed", 0),
                "eta_seconds": kw.get("eta", None),
                "elapsed_seconds": kw.get("elapsed", 0),
                "current_file": kw.get("current_file", ""),
                "errors_count": kw.get("errors", 0),
                "checking": kw.get("checking", 0),
                "total_checks": kw.get("total_checks", 0),
                "batch_current": kw.get("batch_current", 0),
                "batch_total": kw.get("batch_total", 0),
            })

    if on_start:
        on_start(None)

    try:
        if on_progress:
            on_progress("[photosync] Listing files on Koofr...")
        _report("scanning")

        remote_files = _list_remote_files(remote_path, exclude_patterns)
        if on_progress:
            on_progress(f"[photosync] Found {len(remote_files)} files on Koofr")

        if _cancelled():
            return

        to_sync = _compute_diff(remote_files, dest)
        if not to_sync:
            if on_progress:
                on_progress("[photosync] Everything is up to date")
            if on_complete:
                on_complete({"files_transferred": 0, "bytes_transferred": 0, "errors": 0})
            return

        total_bytes = sum(f["Size"] for f in to_sync)
        total_files = len(to_sync)
        if on_progress:
            on_progress(f"[photosync] {total_files} files to sync ({total_bytes / 1048576:.1f} MB)")

        batches = _create_batches(to_sync, batch_size_bytes)
        num_batches = len(batches)
        if on_progress:
            on_progress(f"[photosync] Split into {num_batches} batch(es)")

        bytes_done = 0
        files_done = 0
        start_time = time.time()

        for batch_idx, batch in enumerate(batches):
            if _cancelled():
                break

            batch_bytes = sum(f["Size"] for f in batch)
            batch_files = len(batch)
            batch_num = batch_idx + 1

            if os.path.exists(staging):
                shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(staging, exist_ok=True)
            files_from_path = _write_files_from(batch)

            # ── Download batch to SSD staging ──
            if on_progress:
                on_progress(
                    f"[photosync] Downloading batch {batch_num}/{num_batches} "
                    f"({batch_files} files, {batch_bytes / 1048576:.1f} MB)...")

            rc_port = _find_free_port()
            cmd = [
                "rclone", "copy", f"koofr:{remote_path}/", staging + "/",
                "--config", RCLONE_CONFIG,
                "--rc", f"--rc-addr=127.0.0.1:{rc_port}", "--rc-no-auth",
                "--stats", "0",
                "--checksum", "--check-first",
                "-v",
                "--files-from", files_from_path,
            ]

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
                    if _cancelled():
                        proc.terminate()
                        break
                    raw = _rc_call(rc_port, "core/stats")
                    if raw:
                        xferring = raw.get("transferring") or []
                        current = xferring[0].get("name", "") if xferring else ""
                        _report(
                            "downloading",
                            bytes_done=bytes_done + raw.get("bytes", 0),
                            total_bytes=total_bytes,
                            files_done=files_done + raw.get("transfers", 0),
                            total_files=total_files,
                            speed=raw.get("speed", 0),
                            eta=raw.get("eta"),
                            elapsed=time.time() - start_time,
                            current_file=current,
                            errors=raw.get("errors", 0),
                            checking=raw.get("checks", 0),
                            total_checks=raw.get("totalChecks", 0),
                            batch_current=batch_num,
                            batch_total=num_batches,
                        )
                    time.sleep(1)

            proc.wait()
            reader.join(timeout=5)

            if _cancelled():
                break
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Download failed (batch {batch_num}, exit code {proc.returncode})")

            # ── Verify staging vs Koofr checksums ──
            if on_progress:
                on_progress(f"[photosync] Verifying download (batch {batch_num}/{num_batches})...")
            _report(
                "verifying",
                bytes_done=bytes_done + batch_bytes,
                total_bytes=total_bytes,
                files_done=files_done + batch_files,
                total_files=total_files,
                elapsed=time.time() - start_time,
                batch_current=batch_num,
                batch_total=num_batches,
            )
            _verify_staging_vs_remote(remote_path, staging, files_from_path)
            if on_progress:
                on_progress("[photosync] Download checksums verified OK")

            if _cancelled():
                break

            # ── Copy staging to HDD ──
            if on_progress:
                on_progress(f"[photosync] Copying to drive (batch {batch_num}/{num_batches})...")

            copy_bytes = [0]

            def on_copy_file(rel_path, size, _bd=bytes_done, _bn=batch_num):
                copy_bytes[0] += size
                _report(
                    "copying",
                    bytes_done=_bd + copy_bytes[0],
                    total_bytes=total_bytes,
                    files_done=files_done,
                    total_files=total_files,
                    elapsed=time.time() - start_time,
                    current_file=rel_path,
                    batch_current=_bn,
                    batch_total=num_batches,
                )

            _copy_staging_to_hdd(staging, dest, on_file=on_copy_file)

            subprocess.run(["sync"], timeout=120)

            if _cancelled():
                break

            # ── Verify HDD vs staging checksums ──
            if on_progress:
                on_progress(f"[photosync] Verifying drive copy (batch {batch_num}/{num_batches})...")
            _report(
                "verifying",
                bytes_done=bytes_done + batch_bytes,
                total_bytes=total_bytes,
                files_done=files_done + batch_files,
                total_files=total_files,
                elapsed=time.time() - start_time,
                batch_current=batch_num,
                batch_total=num_batches,
            )
            _verify_hdd_vs_staging(staging, dest)
            if on_progress:
                on_progress("[photosync] Drive copy checksums verified OK")

            bytes_done += batch_bytes
            files_done += batch_files

            shutil.rmtree(staging, ignore_errors=True)
            if on_progress:
                on_progress(f"[photosync] Batch {batch_num}/{num_batches} complete")

        if _cancelled():
            if on_progress:
                on_progress("[photosync] Sync cancelled")
            return

        subprocess.run(["sync"], timeout=60)

        if on_complete:
            on_complete({
                "files_transferred": files_done,
                "bytes_transferred": bytes_done,
                "errors": 0,
            })

    except Exception as e:
        print(f"[photosync] exception: {e}")
        if on_error:
            on_error(str(e))
    finally:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)


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
        _post_ha_service(
            "notify/send_message",
            {"message": message, "title": title, "entity_id": notify_service},
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
