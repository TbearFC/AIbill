#!/bin/sh
# Python 3.9+ is the only prerequisite; no package download is required.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
exec python3 -m agentcost dashboard "$@"
