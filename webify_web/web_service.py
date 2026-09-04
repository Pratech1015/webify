"""Entry point for ``python -m webify_web.web_service``."""
import sys
from webify_web.web import run_web

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_web(port=port)
