#!/usr/bin/env bash
# Mac: double-click this file in Finder. If macOS blocks it, see docs/START_HERE.md.
cd "$(dirname "$0")" || exit 1
bash "packaging/unix/install_unix.sh" "$@"
CODE=$?
echo
read -r -p 'Press Enter to close this window...' _ 2>/dev/null </dev/tty || true
exit $CODE
