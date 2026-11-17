#!/usr/bin/env python3
"""Launch the local workspace with its optional raster environment when present."""
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
venv = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
python = str(venv) if venv.is_file() else sys.executable
arguments = sys.argv[1:]
if not any(value == "--port" or value.startswith("--port=") for value in arguments):
    arguments = ["--port", "8016"] + arguments
os.execv(python, [python, str(root / "server.py")] + arguments)
