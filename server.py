#!/usr/bin/env python3
"""Repository-root entry point for hosting platforms such as Render.

The actual application remains in ./fitretain so its static assets and default
SQLite database continue to live beside the existing application source.
"""
import os
import runpy
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent / "fitretain"
APP_SERVER = APP_DIR / "server.py"

if not APP_SERVER.is_file():
    raise SystemExit(f"FitRetain application server not found: {APP_SERVER}")

# Keep relative paths (including an explicitly relative FITRETAIN_DB) anchored
# to the existing app directory, just as when running fitretain/server.py.
os.chdir(APP_DIR)
runpy.run_path(str(APP_SERVER), run_name="__main__")
