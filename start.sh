#!/bin/sh
# One-command start: creates the environment on first use, then opens the app.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    PYTHON=""
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo "Python 3.11 or newer is required and was not found." >&2
        exit 1
    fi
    if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        echo "Python 3.11 or newer is required; found $("$PYTHON" --version)." >&2
        exit 1
    fi
    echo "First start: creating the environment and installing dependencies..."
    "$PYTHON" -m venv .venv
    .venv/bin/pip install --quiet -r requirements.txt
fi

exec .venv/bin/streamlit run app/main.py "$@"
