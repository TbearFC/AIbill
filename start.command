#!/bin/sh
# Double-click in macOS Finder, or run this script in a terminal.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$PROJECT_DIR/start.sh" "$@"
