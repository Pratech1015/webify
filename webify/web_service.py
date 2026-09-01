"""Entry point for the Webify web dashboard systemd unit.

Running as: python -m webify.web_service <port>

Uses waitress (production) rather than Flask's dev server.
"""

import sys

from .web import run_web


def main():
    port = None
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_web(port=port)


if __name__ == "__main__":
    main()
