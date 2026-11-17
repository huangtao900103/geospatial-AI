"""Repository paths shared by backend modules, independent of process cwd."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
