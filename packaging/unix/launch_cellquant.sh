#!/usr/bin/env bash
# Start CellQuant with one of the installed Cellpose engines (Mac and Linux).
# Started by "Open CellQuant.command" (Mac) or "Open CellQuant.sh" (Linux).
# Asks which engine to use when both are installed. Mirrors
# packaging/windows/launch_cellquant.ps1.
#
# Usage: launch_cellquant.sh [--engine ask|default|cellpose4|cellpose3] [EXPERIMENT_FOLDER]

set -u

CQ_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$CQ_SCRIPT_DIR/../.." && pwd)
# shellcheck source=common.sh
. "$CQ_SCRIPT_DIR/common.sh"

ENGINE='ask'
EXPERIMENT=''
while [ $# -gt 0 ]; do
    case "$1" in
        --engine) ENGINE="$2"; shift 2 ;;
        *) EXPERIMENT="$1"; shift ;;
    esac
done

if [ "$(uname -s)" = 'Darwin' ]; then INSTALLER='Install CellQuant.command'; else INSTALLER='Install CellQuant.sh'; fi

CQ_CONDA=$(cq_resolve_conda)
if [ -z "$CQ_CONDA" ]; then
    echo
    echo 'Conda was not found.'
    echo "Run \"$INSTALLER\" first. It installs Miniforge if this computer has no conda."
    exit 1
fi
CQ_RECORD_PYTHON=$(cq_conda_base_python)
INSTALLED=$(cq_record installed)
if [ -z "$INSTALLED" ]; then
    echo
    echo 'CellQuant is not installed on this computer yet.'
    echo "Run \"$INSTALLER\" first."
    exit 1
fi

KEYS=$(echo "$INSTALLED" | cut -f1)
DEFAULT_ENGINE=$(echo "$KEYS" | head -n 1)
SAVED_DEFAULT=$(cq_record get default_engine)
RECOMMENDED=$(cq_record get recommended_engine)
if [ -n "$SAVED_DEFAULT" ] && echo "$KEYS" | grep -qx "$SAVED_DEFAULT"; then
    DEFAULT_ENGINE="$SAVED_DEFAULT"
fi

COUNT=$(echo "$KEYS" | grep -c .)
if [ "$ENGINE" = 'ask' ] && [ "$COUNT" -gt 1 ] && [ -t 0 ]; then
    echo
    echo 'Which Cellpose engine?'
    NUMBER=0
    DEFAULT_NUMBER=1
    for KEY in $KEYS; do
        NUMBER=$((NUMBER + 1))
        NOTE=''
        [ "$KEY" = 'cellpose4' ] && NOTE=' - most accurate; best with an NVIDIA GPU'
        [ "$KEY" = 'cellpose3' ] && NOTE=' - lighter; faster without a GPU'
        [ "$KEY" = "$RECOMMENDED" ] && NOTE="$NOTE (recommended for this computer)"
        [ "$KEY" = "$DEFAULT_ENGINE" ] && DEFAULT_NUMBER=$NUMBER
        echo "  $NUMBER) $(cq_engine_label "$KEY")$NOTE"
    done
    read -r -p "Choose [1-$COUNT] (Enter = $DEFAULT_NUMBER) " ANSWER || ANSWER=''
    ENGINE="$DEFAULT_ENGINE"
    case "$ANSWER" in
        ''|*[!0-9]*) ;;
        *) if [ "$ANSWER" -ge 1 ] && [ "$ANSWER" -le "$COUNT" ]; then ENGINE=$(echo "$KEYS" | sed -n "${ANSWER}p"); fi ;;
    esac
elif [ "$ENGINE" = 'ask' ] || [ "$ENGINE" = 'default' ]; then
    ENGINE="$DEFAULT_ENGINE"
fi

PREFIX=$(echo "$INSTALLED" | awk -F '\t' -v e="$ENGINE" '$1 == e {print $2}')
if [ -z "$PREFIX" ]; then
    echo
    echo "$(cq_engine_label "$ENGINE") is not installed on this computer."
    echo "Run \"$INSTALLER\", choose [U] Update, and select it."
    exit 1
fi

if [ "$(uname -s)" = 'Linux' ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo
    echo 'No screen was found (DISPLAY is not set). Start CellQuant from a desktop session.'
    exit 1
fi

echo
echo "Starting CellQuant with $(cq_engine_label "$ENGINE")..."
echo "  Environment: $PREFIX"
echo '  The napari window can take a minute to appear. Keep this window open while you work.'
cd "$PROJECT_ROOT" || exit 1
if [ -n "$EXPERIMENT" ]; then
    exec "$CQ_CONDA" run --no-capture-output --prefix "$PREFIX" python -m cellquant "$EXPERIMENT"
fi
exec "$CQ_CONDA" run --no-capture-output --prefix "$PREFIX" python -m cellquant
