#!/bin/bash
# Filament IQ — Dashboard Setup
# Wrapper that calls the Node.js setup script.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
node "$SCRIPT_DIR/setup-dashboard.mjs" "$@"
