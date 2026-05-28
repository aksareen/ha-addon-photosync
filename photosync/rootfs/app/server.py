import json
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

from detect import eject_device, get_drives, mount_device, scan_and_mount
from sync import cancel_sync, pause_sync, resume_sync, run_sync, send_notification

app = Flask(__name__)

OPTIONS_PATH = "/data/options.json"

with open(OPTIONS_PATH) as f:
    options = json.load(f)

FOLDER_NAME = options.get("folder_name", "PhotoSync")
REMOTE_PATH = options.get("remote_path", "/PhotoSync")
NOTIFY_SERVICE = options.get("notify_service", "")
EXCLUDE_PATTERNS = options.get("exclude_patterns", [])

MAX_LOG_LINES = 200

sync_jobs = {}
sync_lock = threading.Lock()


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
        "progress_lines": [],
    }


def _get_job(drive_id):
    with sync_lock:
        if drive_id not in sync_jobs:
            sync_jobs[drive_id] = _fresh_job()
        return sync_jobs[drive_id]


def _find_drive(drive_id):
    return next((d for d in get_drives(FOLDER_NAME) if d["id"] == drive_id), None)


def _drive_with_sync(drive):
    job = _get_job(drive["id"])
    with sync_lock:
        s = dict(job)
        s["progress_lines"] = list(job["progress_lines"])
    drive["sync"] = s
    return drive


# ── Background scanner ──

def _scanner_loop():
    time.sleep(5)
    while True:
        try:
            results = scan_and_mount()
            for r in results:
                print(f"[photosync] auto-mounted {r['device']} at {r['mount_path']}")
        except Exception as e:
            print(f"[photosync] scan error: {e}")
        time.sleep(30)


_scanner = threading.Thread(target=_scanner_loop, daemon=True)
_scanner.start()


# ── Sync thread ──

def _run_sync_thread(drive_id, mount_path, label):
    job = _get_job(drive_id)

    with sync_lock:
        fresh = _fresh_job()
        fresh["status"] = "syncing"
        fresh["phase"] = "scanning"
        fresh["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job.update(fresh)

    send_notification(
        f"Sync started for '{label}'",
        title="PhotoSync",
        notify_service=NOTIFY_SERVICE,
    )

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
            job["percent"] = round((done / total) * 100, 1) if total > 0 else 0

            if stats["total_files"] > 0:
                job["phase"] = "transferring"
            elif stats["checking"] > 0:
                job["phase"] = "scanning"

    def on_complete(stats):
        with sync_lock:
            was_cancelling = job["status"] == "cancelling"
            if was_cancelling:
                job["status"] = "cancelled"
            else:
                job["status"] = "complete"
                job["percent"] = 100
            job["phase"] = "done"
            job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["files_transferred"] = stats.get("files_transferred", job["files_transferred"])
            job["bytes_transferred"] = stats.get("bytes_transferred", job["bytes_transferred"])
            job["errors_count"] = stats.get("errors", job["errors_count"])
            job["current_file"] = ""
            job["speed"] = 0
            job["eta_seconds"] = None

        if not was_cancelling:
            send_notification(
                f"Sync complete for '{label}': "
                f"{stats.get('files_transferred', 0)} files copied. Safe to unplug.",
                title="PhotoSync",
                notify_service=NOTIFY_SERVICE,
            )

    def on_error(error_msg):
        with sync_lock:
            was_cancelling = job["status"] == "cancelling"
            if was_cancelling:
                job["status"] = "cancelled"
                job["error"] = None
            else:
                job["status"] = "failed"
                job["error"] = error_msg
            job["phase"] = "done"
            job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["current_file"] = ""
            job["speed"] = 0
            job["eta_seconds"] = None

        if not was_cancelling:
            send_notification(
                f"Sync FAILED for '{label}': {error_msg}",
                title="PhotoSync",
                notify_service=NOTIFY_SERVICE,
            )

    run_sync(
        drive_label=label,
        mount_path=mount_path,
        folder_name=FOLDER_NAME,
        remote_path=REMOTE_PATH,
        exclude_patterns=EXCLUDE_PATTERNS,
        on_start=on_start,
        on_progress=on_progress,
        on_stats=on_stats,
        on_complete=on_complete,
        on_error=on_error,
    )


# ── Routes ──

@app.route("/")
def index():
    ingress_path = request.headers.get("X-Ingress-Path", "")
    drives = [_drive_with_sync(d) for d in get_drives(FOLDER_NAME)]
    return render_template("index.html", drives=drives, ingress_path=ingress_path)


@app.route("/api/status")
def api_status():
    drives = [_drive_with_sync(d) for d in get_drives(FOLDER_NAME)]
    return jsonify(drives)


@app.route("/api/mount/<drive_id>", methods=["POST"])
def api_mount(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if drive["mounted"]:
        return jsonify({"error": "Already mounted"}), 400
    try:
        mount_path = mount_device(drive["device"])
        return jsonify({"status": "mounted", "mount_path": mount_path})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/eject/<drive_id>", methods=["POST"])
def api_eject(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if not drive["mounted"]:
        return jsonify({"error": "Not mounted"}), 400

    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] in ("syncing", "paused"):
            return jsonify({"error": "Sync in progress — cancel first"}), 409

    try:
        eject_device(drive["mount_path"], drive["device"])
        send_notification(
            f"Drive '{drive['label']}' safely ejected. You can unplug it now.",
            title="PhotoSync",
            notify_service=NOTIFY_SERVICE,
        )
        return jsonify({"status": "ejected"})
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sync/<drive_id>", methods=["POST"])
def trigger_sync(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if not drive["mounted"]:
        return jsonify({"error": "Drive not mounted"}), 400
    if not drive["has_sync_folder"]:
        return jsonify({"error": "PhotoSync folder does not exist"}), 400

    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] in ("syncing", "paused", "cancelling"):
            return jsonify({"error": "Sync already in progress"}), 409

    thread = threading.Thread(
        target=_run_sync_thread,
        args=(drive_id, drive["mount_path"], drive["label"]),
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/pause/<drive_id>", methods=["POST"])
def api_pause(drive_id):
    job = _get_job(drive_id)
    with sync_lock:
        if job["status"] != "syncing":
            return jsonify({"error": "Not syncing"}), 400
        pid = job.get("pid")
        if not pid:
            return jsonify({"error": "No process"}), 400
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
        pid = job.get("pid")
        if not pid:
            return jsonify({"error": "No process"}), 400
        job["status"] = "cancelling"
        if job.get("pid"):
            resume_sync(pid)
        if cancel_sync(pid):
            return jsonify({"status": "cancelling"})
        return jsonify({"error": "Failed to cancel"}), 500


@app.route("/api/create-folder/<drive_id>", methods=["POST"])
def create_folder(drive_id):
    drive = _find_drive(drive_id)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if not drive["mounted"]:
        return jsonify({"error": "Drive not mounted"}), 400
    folder_path = os.path.join(drive["mount_path"], FOLDER_NAME)
    try:
        os.makedirs(folder_path, exist_ok=True)
        return jsonify({"status": "created", "path": folder_path})
    except OSError as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
