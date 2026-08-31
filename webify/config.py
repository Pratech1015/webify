"""Central configuration for Webify."""

import os
from pathlib import Path

# Base data directory (all services live here)
BASE_DIR = Path(os.environ.get("WEBIFY_HOME", Path.home() / ".webify"))
SERVICES_DIR = BASE_DIR / "services"
STATE_FILE = BASE_DIR / "state.json"

# Directories inside each service's folder
REPO_DIR = "repo"          # the cloned source + served root
LOGS_DIR = "logs"

DEFAULT_PORT = 7070
MIN_PORT = 1024
MAX_PORT = 65535

MODES = ("local", "cloudflared", "nginx")

# Console styling
RESET = "\033[0m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"


def ensure_dirs() -> None:
    """Create the directory layout required by Webify."""
    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / LOGS_DIR).mkdir(parents=True, exist_ok=True)


def mode_icon(mode: str) -> str:
    return {
        "local": "🌐",
        "cloudflared": "☁️",
        "nginx": "🚀",
    }.get(mode, "•")
