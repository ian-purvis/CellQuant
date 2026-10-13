#!/usr/bin/env bash
# Linux: run this file from a terminal: bash "Install CellQuant.sh"
cd "$(dirname "$0")" || exit 1
bash "packaging/unix/install_unix.sh" "$@"
CODE=$?
echo
read -r -p 'Press Enter to close this window...' _ 2>/dev/null </dev/tty || true
exit $CODE
