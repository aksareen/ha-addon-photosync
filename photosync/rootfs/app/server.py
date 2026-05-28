import json
import os
import threading
import time

from flask import Flask, jsonify, render_template, request

from detect import get_drives
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


def _get_job(label):
    with sync_lock:
        if label not in sync_jobs:
            sync_jobs[label] = _fresh_job()
        return sync_jobs[label]


def _run_sync_thread(label, mount_path):
    job = _get_job(label)

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


def _drive_with_sync(drive):
    job = _get_job(drive["label"])
    with sync_lock:
        s = dict(job)
        s["progress_lines"] = list(job["progress_lines"])
    drive["sync"] = s
    return drive


@app.route("/")
def index():
    ingress_path = request.headers.get("X-Ingress-Path", "")
    drives = [_drive_with_sync(d) for d in get_drives(FOLDER_NAME)]
    return render_template("index.html", drives=drives, ingress_path=ingress_path)


@app.route("/api/sync/<label>", methods=["POST"])
def trigger_sync(label):
    drives = get_drives(FOLDER_NAME)
    drive = next((d for d in drives if d["label"] == label), None)
    if not drive:
        return jsonify({"error": "Drive not found"}), 404
    if not drive["has_sync_folder"]:
        return jsonify({"error": "PhotoSync folder does not exist"}), 400

    job = _get_job(label)
    with sync_lock:
        if job["status"] in ("syncing", "paused", "cancelling"):
            return jsonify({"error": "Sync already in progress"}), 409

    thread = threading.Thread(
        target=_run_sync_thread, args=(label, drive["mount_path"]), daemon=True,
    )
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/pause/<label>", methods=["POST"])
def api_pause(label):
    job = _get_job(label)
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


@app.route("/api/resume/<label>", methods=["POST"])
def api_resume(label):
    job = _get_job(label)
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


@app.route("/api/cancel/<label>", methods=["POST"])
def api_cancel(label):
    job = _get_job(label)
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
    drives = [_drive_with_sync(d) for d in get_drives(FOLDER_NAME)]
    return jsonify(drives)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
