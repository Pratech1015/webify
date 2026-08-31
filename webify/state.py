"""Persistent state management for Webify services."""

import json
from typing import Dict, List, Optional

from .config import STATE_FILE, ensure_dirs


def _load() -> Dict:
    ensure_dirs()
    if not STATE_FILE.exists():
        return {"services": {}}
    try:
        with STATE_FILE.open("r") as fh:
            data = json.load(fh)
        data.setdefault("services", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"services": {}}


def _save(state: Dict) -> None:
    ensure_dirs()
    # Write atomically to avoid corruption on crash.
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    tmp.replace(STATE_FILE)


def save_service(name: str, info: Dict) -> None:
    state = _load()
    state["services"][name] = info
    _save(state)


def remove_service(name: str) -> None:
    state = _load()
    state["services"].pop(name, None)
    _save(state)


def get_service(name: str) -> Optional[Dict]:
    return _load()["services"].get(name)


def list_services() -> List[Dict]:
    services = _load()["services"]
    return [
        {"name": name, **info}
        for name, info in sorted(services.items())
    ]
