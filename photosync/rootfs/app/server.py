import json
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

from detect import get_drives, safe_eject
from sync import (
    cancel_sync, pause_sync, resume_sync, run_sync, send_notification,
)

app = Flask(__name__)

OPTIONS_PATH = "/data/options.json"

with open(OPTIONS_PATH) as f:
    options = json.load(f)

NOTIFY_SERVICE = options.get("notify_service", "")
AUTO_SYNC_DRIVES = options.get("auto_sync_drives", [])
EXCLUDE_PATTERNS = options.get("exclude_patterns", [])
MIRROR_DELETES = bool(options.get("mirror_deletes", False))

# Sync pairs: each {remote_path, folder_name}. Falls back to the legacy single
# remote_path/folder_name options so existing configs keep working.
SYNC_PAIRS = [
    {"remote_path": p["remote_path"], "folder_name": p["folder_name"]}
    for p in (options.get("sync_pairs") or [])
    if p.get("remote_path") and p.get("folder_name")
]
if not SYNC_PAIRS:
    SYNC_PAIRS = [{
        "remote_path": options.get("remote_path") or "/PhotoSync",
        "folder_name": options.get("folder_name") or "PhotoSync",
    }]

FOLDER_NAMES = [p["folder_name"] for p in SYNC_PAIRS]

MAX_LOG_LINES = 200

sync_jobs = {}
sync_lock = threading.Lock()
cancel_events = {}


def _fresh_job():
    return {
        "status": "idle",
        "phase": None,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "pid": None,
        "total_files": 0,
        "total_bytes": 0,
        "files_transferred": 0,
        "bytes_transferred": 0,
        "percent": 0,
        "speed": 0,
        "eta_seconds": None,
        "elapsed_seconds": 0,
        "current_file": "",
        "errors_count": 0,
        "checking": 0,
        "total_checks": 0,
        "pair_index": 0,
        "pair_total": 0,
        "current_folder": "",
        "mirror": MIRROR_DELETES,
        "progress_lines": [],
    }


def _get_job(drive_id):
    with sync_lock:
        if drive_id not in sync_jobs:
            sync_jobs[drive_id] = _fresh_job()
        return sync_jobs[drive_id]


def _find_drive(drive_id):
    return next((d for d in get_drives(FOLDER_NAMES) if d["id"] == drive_id), None)


def _drive_with_sync(drive):
    job = _get_job(drive["id"])
    with sync_lock:
        s = dict(job)
        s["progress_lines"] = list(job["progress_lines"])
    drive["sync"] = s
    return drive


# ── Sync thread ──

def _run_sync_thread(drive_id, mount_path, label):
    job = _get_job(drive_id)
    cancel_event = threading.Event()

    with sync_lock:
        cancel_events[drive_id] = cancel_event
        fresh = _fresh_job()
        fresh["status"] = "syncing"
        fresh["phase"] = "scanning"
        fresh["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job.update(fresh)

    def on_start(pid):
        with sync_lock:
            job["pid"] = pid

    def on_progress(line):
        with sync_lock:
            lines = job["progress_lines"]
            lines.append(line)
            if len(lines) > MAX_LOG_LINES:
                job["progress_lines"] = lines[-MAX_LOG_LINES:]

    def on_stats(stats):
        with sync_lock:
            if job["status"] not in ("syncing", "paused"):
                return
            job["files_transferred"] = stats["files_transferred"]
            job["bytes_transferred"] = stats["bytes_transferred"]
            job["total_files"] = stats["total_files"]
            job["total_bytes"] = stats["total_bytes"]
            job["speed"] = stats["speed"]
            job["eta_seconds"] = stats["eta_seconds"]
            job["elapsed_seconds"] = stats["elapsed_seconds"]
            job["current_file"] = stats["current_file"]
            job["errors_count"] = stats["errors_count"]
            job["checking"] = stats["checking"]
            job["total_checks"] = stats.get("total_checks", 0)

            total = stats["total_bytes"]
            done = stats["bytes_transferred"]
            job["percent"] = min(round((done / total) * 100, 1), 100) if total > 0 else 0

            job["phase"] = stats.get("phase", job["phase"])

    # Run each configured pair sequentially, accumulating totals across them.
    acc_files = acc_bytes = acc_errors = 0
    final_status = "complete"
    final_error = None

    for idx, pair in enumerate(SYNC_PAIRS):
        if cancel_event.is_set():
            final_status = "cancelled"
            break

        with sync_lock:
            job["pair_index"] = idx + 1
            job["current_folder"] = pair["folder_name"]
            job["phase"] = "scanning"
            # Reset per-pair live counters so the UI reflects the current pair.
            job["files_transferred"] = job["bytes_transferred"] = 0
            job["total_files"] = job["total_bytes"] = 0
            job["percent"] = 0
            job["current_file"] = ""

        result = run_sync(
            drive_label=label,
            mount_path=mount_path,
            folder_name=pair["folder_name"],
            remote_path=pair["remote_path"],
            exclude_patterns=EXCLUDE_PATTERNS,
            mirror=MIRROR_DELETES,
            cancel_event=cancel_event,
            on_start=on_start,
            on_progress=on_progress,
            on_stats=on_stats,
        )

        acc_files += result.get("files_transferred", 0)
        acc_bytes += result.get("bytes_transferred", 0)
        acc_errors += result.get("errors", 0)

        if result["status"] == "cancelled":
            final_status = "cancelled"
            break
        if result["status"] == "failed":
            final_status = "failed"
            final_error = result.get("error") or "unknown error"
            break

    with sync_lock:
        job["status"] = final_status
        job["phase"] = "done"
        job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job["files_transferred"] = acc_files
        job["bytes_transferred"] = acc_bytes
        job["errors_count"] = acc_errors
        job["current_file"] = ""
        job["speed"] = 0
        job["eta_seconds"] = None
        if final_status == "complete":
            job["percent"] = 100
        if final_status == "failed":
            job["error"] = final_error

    if final_status == "complete":
        verb = "synced" if MIRROR_DELETES else "copied"
        if acc_files > 0:
            msg = (f"Sync complete for '{label}': {acc_files} files {verb}, "
                   f"all verified. Safe to unplug.")
        else:
            msg = f"'{label}' is up to date. No changes to sync."
        send_notification(msg, title="PhotoSync", notify_service=NOTIFY_SERVICE)
    elif final_status == "failed":
        send_notification(
            f"Sync FAILED for '{label}': {final_error}",
            title="PhotoSync", notify_service=NOTIFY_SERVICE,
        )


def _start_sync_for_drive(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return False

    for folder_name in FOLDER_NAMES:
        os.makedirs(os.path.join(drive["mount_path"], folder_name), exist_ok=True)

    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] in ("syncing", "paused", "cancelling"):
            return False

    thread = threading.Thread(
        target=_run_sync_thread,
        args=(drive_id, drive["mount_path"], drive["label"]),
        daemon=True,
    )
    thread.start()
    return True


# ── Drive watcher (auto-sync) ──

def _drive_watcher():
    known = set()
    for d in get_drives(FOLDER_NAMES):
        if d["id"] in AUTO_SYNC_DRIVES:
            print(f"[photosync] Drive '{d['id']}' already connected — syncing")
            _start_sync_for_drive(d["id"])
        known.add(d["id"])
    print(f"[photosync] Drive watcher: {len(known)} drive(s) connected")

    while True:
        time.sleep(10)
        try:
            current_drives = get_drives(FOLDER_NAMES)
            current_ids = {d["id"] for d in current_drives}
            new_ids = current_ids - known
            known = current_ids

            for drive_id in new_ids:
                if drive_id not in AUTO_SYNC_DRIVES:
                    print(f"[photosync] Drive '{drive_id}' mounted (not in auto-sync list)")
                    continue

                print(f"[photosync] Auto-sync drive '{drive_id}' detected, waiting 10s...")
                time.sleep(10)

                drive = _find_drive(drive_id)
                if not drive:
                    continue

                _start_sync_for_drive(drive_id)

        except Exception as e:
            print(f"[photosync] Drive watcher error: {e}")


if AUTO_SYNC_DRIVES:
    watcher_thread = threading.Thread(target=_drive_watcher, daemon=True)
    watcher_thread.start()
    print(f"[photosync] Drive watcher started for: {AUTO_SYNC_DRIVES}")


# ── Routes ──

@app.route("/")
def index():
    ingress_path = request.headers.get("X-Ingress-Path", "")
    drives = [_drive_with_sync(d) for d in get_drives(FOLDER_NAMES)]
    return render_template(
        "index.html", drives=drives, ingress_path=ingress_path,
        mirror=MIRROR_DELETES,
    )


@app.route("/api/status")
def api_status():
    drives = [_drive_with_sync(d) for d in get_drives(FOLDER_NAMES)]
    return jsonify(drives)


@app.route("/api/eject/<drive_id>", methods=["POST"])
def api_eject(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] in ("syncing", "paused"):
            return jsonify({"error": "Sync in progress — cancel first"}), 409

    safe_eject(drive["mount_path"])
    return jsonify({"status": "ejected"})


@app.route("/api/sync/<drive_id>", methods=["POST"])
def trigger_sync(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if not drive["has_sync_folder"]:
        return jsonify({"error": "PhotoSync folder does not exist"}), 400

    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] in ("syncing", "paused", "cancelling"):
            return jsonify({"error": "Sync already in progress"}), 409

    _start_sync_for_drive(drive_id)
    return jsonify({"status": "started"})


@app.route("/api/pause/<drive_id>", methods=["POST"])
def api_pause(drive_id):
    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] != "syncing":
            return jsonify({"error": "Not syncing"}), 400
        pid = job.get("pid")
        if not pid:
            return jsonify({"error": "No process to pause"}), 400
        if pause_sync(pid):
            job["status"] = "paused"
            return jsonify({"status": "paused"})
        return jsonify({"error": "Failed to pause"}), 500


@app.route("/api/resume/<drive_id>", methods=["POST"])
def api_resume(drive_id):
    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] != "paused":
            return jsonify({"error": "Not paused"}), 400
        pid = job.get("pid")
        if not pid:
            return jsonify({"error": "No process"}), 400
        if resume_sync(pid):
            job["status"] = "syncing"
            return jsonify({"status": "resumed"})
        return jsonify({"error": "Failed to resume"}), 500


@app.route("/api/cancel/<drive_id>", methods=["POST"])
def api_cancel(drive_id):
    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] not in ("syncing", "paused"):
            return jsonify({"error": "Not syncing"}), 400
        job["status"] = "cancelling"
        pid = job.get("pid")

    cancel_event = cancel_events.get(drive_id)
    if cancel_event:
        cancel_event.set()

    if pid:
        resume_sync(pid)
        cancel_sync(pid)

    return jsonify({"status": "cancelling"})


@app.route("/api/create-folder/<drive_id>", methods=["POST"])
def create_folder(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    try:
        created = []
        for folder_name in FOLDER_NAMES:
            path = os.path.join(drive["mount_path"], folder_name)
            os.makedirs(path, exist_ok=True)
            created.append(path)
        return jsonify({"status": "created", "paths": created})
    except OSError as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
