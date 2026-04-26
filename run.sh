#!/usr/bin/env bash
# Convenience wrapper: activate venv and run daily_fitness.py with any args passed through.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
exec "$HERE/.venv/bin/python" "$HERE/daily_fitness.py" "$@"
