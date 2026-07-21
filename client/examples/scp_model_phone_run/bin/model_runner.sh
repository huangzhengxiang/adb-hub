#!/system/bin/sh
# Portable stand-in for an Android native model executable.  Replace this
# file with the real runner (and add its .so files under lib/) in a real test.
set -eu

MODEL=$1
PROMPT=$2
OUTPUT=$3

[ -r "$MODEL" ]
[ -r "$PROMPT" ]

{
    printf 'runner=adb-hub-portable-model-smoke\n'
    printf 'model_path=%s\n' "$MODEL"
    printf 'model_bytes='
    wc -c < "$MODEL" | tr -d ' '
    printf '\n'
    printf 'prompt='
    tr '\n' ' ' < "$PROMPT"
    printf '\n'
} > "$OUTPUT"

printf 'runner completed; output=%s\n' "$OUTPUT"
