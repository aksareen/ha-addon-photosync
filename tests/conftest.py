import os
import sys

# Add the app directory to sys.path so `from detect import ...` and
# `from sync import ...` resolve the same way they do inside the container.
APP_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, "photosync", "rootfs", "app"
)
sys.path.insert(0, os.path.abspath(APP_DIR))
