import json
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

from detect import get_drives
from sync import run_sync, send_notification

app = Flask(__name__)

OPTIONS_PATH = "/data/options.json"

with open(OPTIONS_PATH) as f:
    options = json.load(f)

FOLDER_NAME = options.get("folder_name", "PhotoSync")
REMOTE_PATH = options.get("remote_path", "/PhotoSync")
NOTIFY_SERVICE = options.get("notify_service", "")
EXCLUDE_PATTERNS = options.get("exclude_patterns", [])

MAX_LOG_LINES = 100

sync_jobs = {}
sync_lock = threading.Lock()


def _get_job(label):
    with sync_lock:
        if label not in sync_jobs:
            sync_jobs[label] = {
                "status": "idle",
                "progress_lines": [],
                "started_at": None,
                "completed_at": None,
                "error": None,
                "files_transferred": 0,
                "bytes_transferred": 0,
            }
        return sync_jobs[label]


def _run_sync_thread(label, mount_path):
    job = _get_job(label)
    with sync_lock:
        job["status"] = "syncing"
        job["progress_lines"] = []
        job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        job["completed_at"] = None
        job["error"] = None

    send_notification(
        f"Sync started for drive '{label}'",
        title="PhotoSync",
        notify_service=NOTIFY_SERVICE,
    )

    def on_progress(line):
        with sync_lock:
            lines = job["progress_lines"]
            lines.append(line)
            if len(lines) > MAX_LOG_LINES:
                job["progress_lines"] = lines[-MAX_LOG_LINES:]

    def on_complete(stats):
        with sync_lock:
            job["status"] = "complete"
            job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["files_transferred"] = stats.get("files_transferred", 0)
            job["bytes_transferred"] = stats.get("bytes_transferred", 0)
        send_notification(
            f"Sync complete for drive '{label}'. "
            f"Files: {stats.get('files_transferred', 0)}, "
            f"Errors: {stats.get('errors', 0)}. "
            f"Safe to unplug.",
            title="PhotoSync",
            notify_service=NOTIFY_SERVICE,
        )

    def on_error(error_msg):
        with sync_lock:
            job["status"] = "failed"
            job["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["error"] = error_msg
        send_notification(
            f"Sync FAILED for drive '{label}': {error_msg}",
            title="PhotoSync",
            notify_service=NOTIFY_SERVICE,
        )

    run_sync(
        drive_label=label,
        mount_path=mount_path,
        folder_name=FOLDER_NAME,
        remote_path=REMOTE_PATH,
        exclude_patterns=EXCLUDE_PATTERNS,
        on_progress=on_progress,
        on_complete=on_complete,
        on_error=on_error,
    )


@app.route("/")
def index():
    ingress_path = request.headers.get("X-Ingress-Path", "")
    drives = get_drives(FOLDER_NAME)

    drives_with_status = []
    for drive in drives:
        job = _get_job(drive["label"])
        with sync_lock:
            drive["sync"] = dict(job)
        drives_with_status.append(drive)

    return render_template("index.html", drives=drives_with_status, ingress_path=ingress_path)


@app.route("/api/sync/<label>", methods=["POST"])
def trigger_sync(label):
    drives = get_drives(FOLDER_NAME)
    drive = next((d for d in drives if d["label"] == label), None)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    if not drive["has_sync_folder"]:
        return jsonify({"error": "PhotoSync folder does not exist on this drive"}), 400

    job = _get_job(label)
    with sync_lock:
        if job["status"] == "syncing":
            return jsonify({"error": "Sync already in progress"}), 409

    thread = threading.Thread(
        target=_run_sync_thread,
        args=(label, drive["mount_path"]),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started"})


@app.route("/api/create-folder/<label>", methods=["POST"])
def create_folder(label):
    drives = get_drives(FOLDER_NAME)
    drive = next((d for d in drives if d["label"] == label), None)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404

    folder_path = os.path.join(drive["mount_path"], FOLDER_NAME)
    try:
        os.makedirs(folder_path, exist_ok=True)
        return jsonify({"status": "created", "path": folder_path})
    except OSError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/status")
def api_status():
    drives = get_drives(FOLDER_NAME)

    result = []
    for drive in drives:
        job = _get_job(drive["label"])
        with sync_lock:
            drive["sync"] = dict(job)
        result.append(drive)

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
