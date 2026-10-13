#!/usr/bin/env bash
# CellQuant installer for Mac and Linux. Started by "Install CellQuant.command"
# (Mac) or "Install CellQuant.sh" (Linux). Mirrors the Windows installer
# (packaging/windows/install_windows.ps1):
#   - finds conda, or offers to download and install Miniforge for this user;
#   - builds one conda environment per Cellpose engine the user chooses:
#       Cellpose-SAM (Cellpose 4)       most accurate; best with an NVIDIA GPU
#       Classic Cellpose (Cellpose 3)   lighter; faster on computers without a GPU
#   - downloads the default model and checks each environment with a test analysis.
#
# Options: --parent DIR  --engines both|cellpose4|cellpose3  --non-interactive  --dev  --log FILE

set -u

CQ_SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$CQ_SCRIPT_DIR/../.." && pwd)
WINDOWS_DIR="$PROJECT_ROOT/packaging/windows"
# shellcheck source=common.sh
. "$CQ_SCRIPT_DIR/common.sh"

PARENT=''
ENGINES='ask'
NON_INTERACTIVE=0
DEV=0
LOG="$PROJECT_ROOT/install_last.log"
while [ $# -gt 0 ]; do
    case "$1" in
        --parent) PARENT="$2"; shift 2 ;;
        --engines) ENGINES="$2"; shift 2 ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        --dev) DEV=1; shift ;;
        --log) LOG="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 2 ;;
    esac
done
case "$ENGINES" in
    ask|both|cellpose4|cellpose3) ;;
    *) echo "--engines must be both, cellpose4 or cellpose3"; exit 2 ;;
esac

# Everything shown is also written to the log.
: > "$LOG" 2>/dev/null || LOG="${TMPDIR:-/tmp}/cellquant_install_last.log"
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "$*"; }
warn() { echo "  $*"; }

ask_choice() {
    # ask_choice PROMPT DEFAULT ALLOWED...  -> prints the answer (upper case)
    local prompt="$1" default="$2" answer allowed
    shift 2
    if [ "$NON_INTERACTIVE" = 1 ]; then
        echo "$default"
        return
    fi
    while true; do
        read -r -p "$prompt " answer </dev/tty || answer=''
        answer=$(echo "$answer" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')
        if [ -z "$answer" ]; then
            echo "$default"
            return
        fi
        for allowed in "$@"; do
            if [ "$answer" = "$allowed" ]; then
                echo "$answer"
                return
            fi
        done
        echo "Please type one of: $*" >/dev/tty
    done
}

fail() {
    echo
    echo "CellQuant install did not finish."
    echo "$*"
    echo
    echo "Full log: $LOG"
    exit 1
}

sha256_of() {
    if type -P shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

download() {
    if type -P curl >/dev/null 2>&1; then
        curl --location --fail --silent --show-error --output "$2" "$1"
    elif type -P wget >/dev/null 2>&1; then
        wget --quiet --output-document="$2" "$1"
    else
        echo "Neither curl nor wget is installed."
        return 1
    fi
}

install_miniforge() {
    # install_miniforge TARGET -> installs Miniforge there, without changing PATH or shell settings.
    local target="$1" platform file url expected tmp actual
    platform=$(cq_platform)
    expected=$(cq_miniforge_sha256 "$platform")
    if [ -z "$expected" ]; then
        echo "Miniforge is not available for this computer ($platform)."
        return 1
    fi
    file="Miniforge3-$CQ_MINIFORGE_VERSION-$platform.sh"
    url="https://github.com/conda-forge/miniforge/releases/download/$CQ_MINIFORGE_VERSION/$file"
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/cellquant-miniforge.XXXXXX") || return 1
    echo "  Downloading Miniforge $CQ_MINIFORGE_VERSION for $platform (about 100 MB)..."
    if ! download "$url" "$tmp/$file"; then
        rm -rf "$tmp"
        echo "Download failed: $url"
        return 1
    fi
    actual=$(sha256_of "$tmp/$file")
    if [ "$actual" != "$expected" ]; then
        rm -rf "$tmp"
        echo "The Miniforge download did not match its expected checksum (got $actual). Try again; if it keeps failing, install Miniforge yourself."
        return 1
    fi
    echo "  Installing Miniforge to $target (a few minutes)..."
    # -b: no questions, and no changes to PATH or shell startup files.
    if ! bash "$tmp/$file" -b -p "$target"; then
        rm -rf "$tmp"
        echo "The Miniforge installer did not finish."
        return 1
    fi
    rm -rf "$tmp"
    if [ ! -x "$target/bin/conda" ]; then
        echo "The Miniforge installer finished, but $target/bin/conda was not created."
        return 1
    fi
}

echo
echo 'CellQuant installer'
echo '-------------------'
echo "Log: $LOG"

# --- conda ---------------------------------------------------------------
CQ_CONDA=$(cq_resolve_conda)
if [ -z "$CQ_CONDA" ]; then
    TARGET="$HOME/miniforge3"
    PROBLEM=''
    case "$TARGET" in
        *' '*) PROBLEM="Your home folder has a space in its name ($HOME), and Miniforge cannot be installed in such a folder." ;;
    esac
    if [ -z "$PROBLEM" ] && [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
        PROBLEM="$TARGET already exists but has no conda in it. Move or delete it, then run the installer again."
    fi
    if [ -z "$PROBLEM" ]; then
        echo
        echo 'Conda was not found on this computer. CellQuant needs it to install Python and Cellpose.'
        echo "The installer can download Miniforge (free, from conda-forge) and install it to $TARGET."
        echo 'It is installed for this user only and does not change other software.'
        CHOICE=$(ask_choice 'Install Miniforge now? [Y/N] (Enter = Y)' Y Y N)
        if [ "$CHOICE" = 'N' ]; then
            PROBLEM='You chose not to install Miniforge.'
        else
            step 'Installing Miniforge...'
            if install_miniforge "$TARGET"; then
                CQ_CONDA="$TARGET/bin/conda"
            else
                PROBLEM='Miniforge could not be installed (see the message above).'
            fi
        fi
    fi
    if [ -z "$CQ_CONDA" ]; then
        fail "Conda was not found on this computer. $PROBLEM
Install Miniforge (recommended) or Miniconda yourself, then run the installer again.
Miniforge: https://conda-forge.org/download/"
    fi
fi
echo "Using conda: $CQ_CONDA"
CQ_RECORD_PYTHON=$(cq_conda_base_python)
[ -n "$CQ_RECORD_PYTHON" ] || fail "Could not find the Python that comes with conda ($CQ_CONDA)."

# --- this computer -------------------------------------------------------
step 'Checking this computer...'
OS=$(uname -s)
GPU_NAME=''
GPU_GB=''
if [ "$OS" = 'Linux' ] && type -P nvidia-smi >/dev/null 2>&1; then
    GPU_LINE=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -n 1)
    if [ -n "$GPU_LINE" ]; then
        GPU_NAME=$(echo "$GPU_LINE" | cut -d, -f1 | sed 's/^ *//; s/ *$//')
        GPU_GB=$(echo "$GPU_LINE" | cut -d, -f2 | awk '{printf "%d", ($1 / 1024) + 0.5}')
    fi
fi
if [ "$OS" = 'Darwin' ]; then
    RAM_GB=$(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1 / 1073741824 + 0.5}')
else
    RAM_GB=$(awk '/MemTotal/ {printf "%d", $2 / 1048576 + 0.5}' /proc/meminfo 2>/dev/null)
fi
if [ -n "$GPU_NAME" ]; then
    echo "  NVIDIA GPU: $GPU_NAME, $GPU_GB GB"
else
    echo '  NVIDIA GPU: none found. Cellpose will run on the CPU.'
fi
if [ -n "$RAM_GB" ]; then
    echo "  Memory: $RAM_GB GB"
    if [ "$RAM_GB" -lt 16 ]; then
        warn 'Less than 16 GB of memory: large Z-stacks may not fit. Use the 2D modes for them.'
    fi
fi
# Same rule as Windows: Cellpose-SAM is slow without an NVIDIA GPU, and may run
# out of memory on one with less than about 6 GB.
if [ -n "$GPU_NAME" ] && [ "${GPU_GB:-0}" -ge 6 ]; then
    RECOMMENDED='cellpose4'
    REASON="this computer has an NVIDIA GPU ($GPU_NAME) that Cellpose-SAM can use"
elif [ -n "$GPU_NAME" ]; then
    RECOMMENDED='cellpose3'
    REASON="the GPU has $GPU_GB GB of memory, and Cellpose-SAM may run out of GPU memory"
else
    RECOMMENDED='cellpose3'
    REASON='there is no NVIDIA GPU, and Cellpose-SAM is slow without one, especially for Z-stacks'
fi
echo "  Recommended engine: $(cq_engine_label "$RECOMMENDED"), because $REASON."

# --- existing install ----------------------------------------------------
INSTALLED=$(cq_record installed)
MODE='install'
if [ -n "$INSTALLED" ]; then
    MODE='update'
    if [ "$NON_INTERACTIVE" = 0 ]; then
        echo
        echo 'CellQuant is already installed on this computer:'
        echo "$INSTALLED" | while IFS="$(printf '\t')" read -r ENGINE PREFIX; do
            echo "  $(cq_engine_label "$ENGINE"): $PREFIX"
        done
        echo
        echo '  [O] Open CellQuant (change nothing)'
        echo '  [U] Update: install the current CellQuant, and add an engine if you choose one'
        echo '  [R] Reinstall: delete the CellQuant environments and install again'
        CHOICE=$(ask_choice 'Choice [O/U/R] (Enter = O)' O O U R)
        if [ "$CHOICE" = 'O' ]; then
            exec bash "$CQ_SCRIPT_DIR/launch_cellquant.sh"
        fi
        if [ "$CHOICE" = 'R' ]; then
            MODE='reinstall'
        fi
    fi
fi

# --- where the environments go -------------------------------------------
CONDA_BASE=$(cq_conda_base)
if [ -n "$CONDA_BASE" ]; then DEFAULT_PARENT="$CONDA_BASE/envs"; else DEFAULT_PARENT="$HOME/cellquant-envs"; fi
SAVED_PARENT=$(cq_record get parent)
if [ -z "$PARENT" ]; then
    if [ "$MODE" != 'install' ] && [ -n "$SAVED_PARENT" ]; then
        PARENT="$SAVED_PARENT"
    elif [ "$NON_INTERACTIVE" = 1 ]; then
        PARENT="$DEFAULT_PARENT"
    else
        echo
        echo 'Where should the CellQuant environments go?'
        echo "  Default: $DEFAULT_PARENT"
        echo '  Use a folder on this computer, not in iCloud, Dropbox, OneDrive or Google Drive.'
        read -r -p 'Folder (Enter = default, or paste a full path) ' PARENT </dev/tty || PARENT=''
        PARENT=$(echo "$PARENT" | sed "s/^[[:space:]\"']*//; s/[[:space:]\"']*\$//")
        [ -n "$PARENT" ] || PARENT="$DEFAULT_PARENT"
    fi
fi
while true; do
    case "$PARENT" in
        "~"*) PARENT="$HOME${PARENT#\~}" ;;
    esac
    PROBLEM=''
    case "$PARENT" in
        /*) ;;
        *) PROBLEM='Use a full path, starting with /.' ;;
    esac
    case "$PARENT" in
        *' '*) PROBLEM='The path has a space in it, which breaks some Python packages. Choose a folder without spaces.' ;;
    esac
    case "$PARENT" in
        *OneDrive*|*Dropbox*|*'Mobile Documents'*|*CloudStorage*|*'Google Drive'*)
            PROBLEM='This folder is synced to the cloud. Syncing breaks Python environments. Choose a folder outside it.' ;;
    esac
    if [ -z "$PROBLEM" ] && ! mkdir -p "$PARENT" 2>/dev/null; then
        PROBLEM="This account cannot create or write to $PARENT."
    elif [ -z "$PROBLEM" ] && [ ! -w "$PARENT" ]; then
        PROBLEM="This account cannot write to $PARENT."
    fi
    [ -z "$PROBLEM" ] && break
    echo
    echo "CellQuant cannot install into this folder: $PARENT"
    echo "$PROBLEM"
    if [ "$NON_INTERACTIVE" = 1 ] || [ "$PARENT" = "$DEFAULT_PARENT" ]; then
        fail "$PROBLEM"
    fi
    read -r -p "Folder (Enter = default: $DEFAULT_PARENT) " PARENT </dev/tty || PARENT=''
    [ -n "$PARENT" ] || PARENT="$DEFAULT_PARENT"
done
PARENT=$(cd "$PARENT" && pwd)

# --- which engines -------------------------------------------------------
EACH_GB=4
[ -n "$GPU_NAME" ] && EACH_GB=8
FREE_GB=$(df -Pk "$PARENT" 2>/dev/null | awk 'NR == 2 {printf "%d", $4 / 1048576}')
DEFAULT_CHOICE='B'
INSTALLED_COUNT=$(echo "$INSTALLED" | grep -c . || true)
if [ "$MODE" = 'update' ] && [ "$INSTALLED_COUNT" = 1 ]; then
    DEFAULT_CHOICE='S'
    echo "$INSTALLED" | grep -q '^cellpose3' && DEFAULT_CHOICE='C'
elif [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt $((2 * EACH_GB + 5)) ]; then
    DEFAULT_CHOICE='S'
    [ "$RECOMMENDED" = 'cellpose3' ] && DEFAULT_CHOICE='C'
fi
if [ "$ENGINES" = 'ask' ]; then
    if [ "$NON_INTERACTIVE" = 1 ]; then
        ENGINES='both'
    else
        echo
        echo 'Which Cellpose engines should be installed?'
        echo '  [B] Both. You choose one each time you open CellQuant.'
        echo '  [S] Cellpose-SAM only (Cellpose 4). Most accurate. Best with an NVIDIA GPU.'
        echo '  [C] Classic Cellpose only (Cellpose 3). Lighter. Faster without a GPU.'
        echo "  Recommended for this computer: $(cq_engine_label "$RECOMMENDED")."
        SPACE_TEXT="  Each engine needs roughly $EACH_GB GB."
        [ -n "$FREE_GB" ] && SPACE_TEXT="$SPACE_TEXT Free space on that drive: $FREE_GB GB."
        echo "$SPACE_TEXT"
        CHOICE=$(ask_choice "Choice [B/S/C] (Enter = $DEFAULT_CHOICE)" "$DEFAULT_CHOICE" B S C)
        case "$CHOICE" in
            S) ENGINES='cellpose4' ;;
            C) ENGINES='cellpose3' ;;
            *) ENGINES='both' ;;
        esac
    fi
fi
if [ "$ENGINES" = 'both' ]; then SELECTED='cellpose4 cellpose3'; else SELECTED="$ENGINES"; fi

# --- install -------------------------------------------------------------
env_python() {
    # env_python PREFIX ARGS...  (output shown live)
    local prefix="$1"
    shift
    "$CQ_CONDA" run --no-capture-output --prefix "$prefix" python "$@"
}

install_engine() {
    # install_engine ENGINE PREFIX -> sets ENGINE_VERSION, ENGINE_GPU, ENGINE_WARNING
    local engine="$1" prefix="$2" label extra major torch_args requirement smoke summary download_output download_code
    label=$(cq_engine_label "$engine")
    extra='cellpose-v4'; major='4'
    if [ "$engine" = 'cellpose3' ]; then extra='cellpose-v3'; major='3'; fi
    ENGINE_VERSION=''; ENGINE_GPU=''; ENGINE_WARNING=''

    step "Installing $label"
    echo "  Folder: $prefix"
    echo '  This can take 10 to 30 minutes, depending on the connection.'

    # 1. Python
    if [ ! -x "$prefix/bin/python" ]; then
        [ -e "$prefix" ] && rm -rf "$prefix"  # left over from a failed install
        step '  Creating the Python 3.11 environment...'
        # conda-forge only: the Anaconda "defaults" channel can stop the install
        # to ask for terms-of-service acceptance.
        "$CQ_CONDA" create --yes --prefix "$prefix" --override-channels --channel conda-forge python=3.11 pip \
            || { echo "Could not create the Python environment for $label"; return 1; }
    fi
    env_python "$prefix" -m pip install --disable-pip-version-check --upgrade pip || { echo 'Could not update pip'; return 1; }

    # 2. PyTorch first. Linux without an NVIDIA GPU gets the CPU build, which
    #    skips about 3 GB of NVIDIA libraries. The Linux default build includes
    #    GPU support; the Mac build is CPU only for Cellpose.
    torch_args='torch'
    [ "$engine" = 'cellpose4' ] && torch_args='torch torchvision'
    if [ "$OS" = 'Linux' ] && [ -z "$GPU_NAME" ]; then
        step '  Installing PyTorch (CPU)...'
        # shellcheck disable=SC2086
        env_python "$prefix" -m pip install --disable-pip-version-check --upgrade $torch_args \
            --index-url https://download.pytorch.org/whl/cpu || { echo 'Could not install PyTorch'; return 1; }
    fi

    # 3. CellQuant, napari and this engine's Cellpose.
    step "  Installing CellQuant, napari and $label..."
    requirement="${PROJECT_ROOT}[gui,$extra]"
    if [ "$DEV" = 1 ]; then
        env_python "$prefix" -m pip install --disable-pip-version-check --upgrade --editable "$requirement" \
            || { echo 'Could not install CellQuant'; return 1; }
    else
        env_python "$prefix" -m pip install --disable-pip-version-check --upgrade "$requirement" \
            || { echo 'Could not install CellQuant'; return 1; }
        # Make sure the copy in the environment matches the project folder, even
        # when the version number did not change.
        env_python "$prefix" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$PROJECT_ROOT" \
            || { echo 'Could not refresh CellQuant'; return 1; }
    fi

    ENGINE_VERSION=$("$prefix/bin/python" -c "import importlib.metadata as m; print(m.version('cellpose'))" 2>/dev/null)
    echo "  Cellpose in this environment: $ENGINE_VERSION"
    case "$ENGINE_VERSION" in
        "$major".*) ;;
        *) echo "Expected Cellpose $major in $prefix but found '$ENGINE_VERSION'. Run the installer again and choose [R] to reinstall."; return 1 ;;
    esac

    step '  Checking installed packages for conflicts...'
    "$prefix/bin/python" -m pip check || { echo "Package conflict in $prefix"; return 1; }

    # 4. Model weights now, not during the first analysis. A failure here is only a warning.
    step '  Downloading the default Cellpose model (Cellpose-SAM is over 1 GB)...'
    download_output=$("$prefix/bin/python" "$WINDOWS_DIR/download_models.py" 2>&1)
    download_code=$?
    echo "$download_output" | tail -n 3 | sed 's/^/  /'
    if [ "$download_code" != 0 ]; then
        ENGINE_WARNING='The Cellpose model could not be downloaded now. CellQuant will download it the first time Cellpose runs, which needs an internet connection.'
        warn "$ENGINE_WARNING"
    fi

    # 5. A small test analysis, then the fastest CPU thread count for Cellpose here.
    step '  Running a test analysis and measuring CPU speed...'
    smoke=$("$prefix/bin/python" "$WINDOWS_DIR/smoke_test.py" "$engine" --benchmark-threads 2>&1)
    summary=$(echo "$smoke" | cq_record smoke)
    case "$summary" in
        ok*)
            echo "  $(echo "$summary" | cut -f2)"
            ENGINE_GPU=$(echo "$summary" | cut -f3) ;;
        *)
            echo "The test analysis failed in $prefix"
            echo "$summary" | cut -f2-
            return 1 ;;
    esac
}

if [ "$MODE" = 'reinstall' ]; then
    step 'Deleting the existing CellQuant environments...'
    cq_record recorded | while IFS="$(printf '\t')" read -r ENGINE PREFIX; do
        echo "Deleting environment: $PREFIX"
        rm -rf "$PREFIX"
    done
    cq_record forget
fi

FAILURES=''
WARNINGS=''
for ENGINE in $SELECTED; do
    PREFIX="$PARENT/cellquant2-$ENGINE"
    EXISTING_PREFIX=$(cq_record installed | awk -F '\t' -v e="$ENGINE" '$1 == e {print $2}')
    if [ "$MODE" = 'update' ] && [ -n "$EXISTING_PREFIX" ]; then
        PREFIX="$EXISTING_PREFIX"
    fi
    if install_engine "$ENGINE" "$PREFIX"; then
        # Saved after each engine so a later failure does not lose a working one.
        cq_record save "$ENGINE" "$PREFIX" "$ENGINE_VERSION" "$ENGINE_GPU" "$ENGINE_WARNING" \
            "$RECOMMENDED" "$RECOMMENDED" "$PARENT" "$PROJECT_ROOT"
        [ -n "$ENGINE_WARNING" ] && WARNINGS="$WARNINGS
Note: $ENGINE_WARNING"
    else
        FAILURES="$FAILURES
$(cq_engine_label "$ENGINE") was not installed (see the messages above)."
        echo
        echo "$(cq_engine_label "$ENGINE") was not installed."
    fi
done

echo
DONE=$(cq_record installed)
if [ -n "$DONE" ]; then
    echo 'Installed:'
    echo "$DONE" | while IFS="$(printf '\t')" read -r ENGINE PREFIX; do
        echo "  $(cq_engine_label "$ENGINE"): $PREFIX"
    done
    echo "Install record: $(cq_record path)"
fi
[ -n "$WARNINGS" ] && echo "$WARNINGS"

if [ -n "$FAILURES" ]; then
    MESSAGE="$FAILURES

Common fixes: check the internet connection; run the installer again and press Enter for the default folder."
    [ -n "$DONE" ] && MESSAGE="$MESSAGE
The engines listed as installed above can already be used."
    fail "$MESSAGE"
fi

if [ "$OS" = 'Darwin' ]; then
    echo 'Install finished. Double-click "Open CellQuant.command" to start.'
else
    echo 'Install finished. Run "Open CellQuant.sh" to start.'
fi
exit 0
