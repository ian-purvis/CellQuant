# Shared by install_unix.sh and launch_cellquant.sh (Mac and Linux).
# Sourced, not run. Written for bash 3.2, the version macOS ships.

# Miniforge is installed for users who have no conda. The version is pinned so
# the download can be checked against a known SHA256 (the same release as the
# Windows installer, packaging/windows/resolve_conda.ps1).
CQ_MINIFORGE_VERSION='26.7.2-0'

cq_miniforge_sha256() {
    case "$1" in
        Linux-x86_64) echo '281b0ac7d550802efc81af633225a5e6116d29ae72f3ab4eae7168c3931a4c05' ;;
        Linux-aarch64) echo '89b786c8d2c8b0fda7553914c1314ae4ddaa094503802f279377b19ac4463cb2' ;;
        MacOSX-arm64) echo 'd70bfa2e97afcda96927c9b9ca0e2316cb7750e4ce651c94388267cbe9588711' ;;
        MacOSX-x86_64) echo 'b00e7798658f92721a3ae2f6b9832695ffc6baf07758894d726268055359f6c5' ;;
        *) echo '' ;;
    esac
}

cq_engine_label() {
    case "$1" in
        cellpose4) echo 'Cellpose-SAM (Cellpose 4)' ;;
        cellpose3) echo 'Classic Cellpose (Cellpose 3)' ;;
        *) echo "$1" ;;
    esac
}

cq_platform() {
    # Miniforge's name for this computer, for example MacOSX-arm64 or Linux-x86_64.
    local system machine
    system=$(uname -s)
    machine=$(uname -m)
    case "$system" in
        Darwin) system='MacOSX' ;;
        Linux) system='Linux' ;;
    esac
    # A Terminal running under Rosetta reports x86_64 on an Apple silicon Mac; use the native
    # Miniforge anyway, so Cellpose can use the Apple GPU.
    if [ "$system" = 'MacOSX' ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = '1' ]; then machine='arm64'; fi
    case "$machine" in
        aarch64|arm64)
            if [ "$system" = 'MacOSX' ]; then machine='arm64'; else machine='aarch64'; fi ;;
        amd64) machine='x86_64' ;;
    esac
    echo "${system}-${machine}"
}

cq_resolve_conda() {
    # Prints the path of a working conda executable, or nothing.
    local candidate
    if [ -n "${CONDA_EXE:-}" ] && [ -x "$CONDA_EXE" ]; then
        echo "$CONDA_EXE"
        return
    fi
    candidate=$(type -P conda 2>/dev/null || true)
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        echo "$candidate"
        return
    fi
    for candidate in \
        "$HOME/miniforge3" "$HOME/Miniforge3" "$HOME/mambaforge" "$HOME/miniconda3" "$HOME/anaconda3" \
        "$HOME/opt/miniconda3" "$HOME/opt/anaconda3" \
        /opt/homebrew/Caskroom/miniforge/base /usr/local/Caskroom/miniforge/base \
        /opt/miniforge3 /opt/miniconda3 /opt/anaconda3 /opt/conda; do
        if [ -x "$candidate/bin/conda" ]; then
            echo "$candidate/bin/conda"
            return
        fi
    done
}

cq_conda_base() {
    # The folder conda is installed in.
    "$CQ_CONDA" info --base 2>/dev/null | tail -n 1
}

cq_conda_base_python() {
    # The Python that ships with conda, used for the install record.
    local base
    base=$(cq_conda_base)
    if [ -n "$base" ] && [ -x "$base/bin/python" ]; then
        echo "$base/bin/python"
    else
        type -P python3 2>/dev/null || true
    fi
}

cq_record() {
    "$CQ_RECORD_PYTHON" "$CQ_SCRIPT_DIR/env_record.py" "$@"
}

# --- Mac hardware ----------------------------------------------------------

cq_apple_silicon() {
    # True on an Apple silicon Mac, also from a Terminal running under Rosetta.
    [ "$(uname -s)" = 'Darwin' ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = '1' ]
}

cq_mac_chip() {
    # For example "Apple M2 Pro" or "Intel(R) Core(TM) i7-8850H CPU @ 2.60GHz".
    sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'unknown chip'
}

cq_mac_memory_gb() {
    sysctl -n hw.memsize 2>/dev/null | awk '{printf "%d", $1 / 1073741824 + 0.5}'
}

cq_intel_only_python() {
    # True when the environment's Python is built for Intel only, so it cannot use the Apple GPU.
    local python="$1/bin/python"
    [ -e "$python" ] || return 1
    file -L "$python" 2>/dev/null | grep -q 'x86_64' && ! file -L "$python" 2>/dev/null | grep -q 'arm64'
}

cq_mac_notice() {
    # What this Mac means for Cellpose, and the options. $1: the environment that will run.
    local memory
    memory=$(cq_mac_memory_gb)
    echo
    echo "This Mac: $(cq_mac_chip)${memory:+, $memory GB memory}."
    if cq_apple_silicon && [ -n "${1:-}" ] && cq_intel_only_python "$1"; then
        echo '  CellQuant was installed for Intel Macs, so Cellpose cannot use the Apple GPU and runs on the CPU (slow).'
        echo '  To fix it: open Terminal normally (not with Rosetta), run "Install CellQuant.command", and choose [R] Reinstall.'
    elif cq_apple_silicon; then
        echo '  Cellpose will use the Apple GPU. 2D (max projection, one slice) and linked slices run well;'
        echo '  full 3D is slower than on an NVIDIA GPU, especially for large stacks.'
        echo '  Options: Cellpose-SAM (most accurate) or Classic Cellpose (faster).'
    else
        echo '  This Mac has no GPU that Cellpose can use, so Cellpose runs on the CPU (slow).'
        echo '  Options: Classic Cellpose (faster without a GPU); max projection rather than 3D;'
        echo '  full 3D or large batches on a computer with an NVIDIA GPU, or on a cluster.'
    fi
    if [ -n "$memory" ] && [ "$memory" -lt 16 ]; then
        echo "  With $memory GB of memory, large Z-stacks may not fit in full 3D. Use the 2D modes for them."
    fi
}
