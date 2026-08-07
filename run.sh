#!/usr/bin/env sh
# Start PEG. Double-click this, or run ./run.sh from a terminal.
#
# Nothing to install: PEG uses only the Python standard library, so this just
# finds a Python 3.11+ and hands it the game.

set -e
cd "$(dirname "$0")"

for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            exec "$candidate" -m peg "$@"
        fi
    fi
done

echo "PEG needs Python 3.11 or newer, and I could not find it."
echo
echo "  macOS:   brew install python3"
echo "  Ubuntu:  sudo apt install python3"
echo "  or:      https://www.python.org/downloads/"
exit 1
