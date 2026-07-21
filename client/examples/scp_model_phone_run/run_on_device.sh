#!/system/bin/sh
# This file runs inside the adb-hub device session workdir.
set -eu

WORKDIR=$(pwd)
RUNNER=${ADB_HUB_RUNNER:-bin/model_runner.sh}
MODEL=${ADB_HUB_MODEL:-model/demo_model.txt}
PROMPT=${ADB_HUB_PROMPT:-input/prompt.txt}
OUTPUT=${ADB_HUB_OUTPUT:-outputs/model_output.txt}

# Keep all runtime state inside the leased adb-hub session directory.  A real
# native runner can put its .so files in lib/ without changing this script.
export LD_LIBRARY_PATH="$WORKDIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$WORKDIR/bin:$PATH"
export TMPDIR="$WORKDIR/tmp"
export HOME="$WORKDIR"
export XDG_CACHE_HOME="$WORKDIR/.cache"

mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" "$(dirname "$OUTPUT")"
chmod +x "$RUNNER"

printf 'adb-hub launcher: runner=%s model=%s prompt=%s\n' "$RUNNER" "$MODEL" "$PROMPT"
case "$RUNNER" in
    *.sh)
        # Android shell scripts commonly use a /system/bin/sh shebang.  The
        # override makes this checked-in smoke runner testable on a POSIX host.
        exec "${ADB_HUB_SHELL:-/system/bin/sh}" "$RUNNER" "$MODEL" "$PROMPT" "$OUTPUT"
        ;;
    *)
        exec "$RUNNER" "$MODEL" "$PROMPT" "$OUTPUT"
        ;;
esac
