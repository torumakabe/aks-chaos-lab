#!/bin/bash

INPUT=$(cat)

SOURCE=$(echo "$INPUT" | jq -r '.source')

TIMESTAMP_MS=$(echo "$INPUT" | jq -r '.timestamp')
TIMESTAMP_SEC=$((TIMESTAMP_MS / 1000))

# macOS uses 'date -r', Linux uses 'date -d @'
if [[ "$(uname)" == "Darwin" ]]; then
    TIMESTAMP=$(date -r "$TIMESTAMP_SEC" '+%Y-%m-%d %H:%M:%S')
else
    TIMESTAMP=$(date -d "@$TIMESTAMP_SEC" '+%Y-%m-%d %H:%M:%S')
fi

echo "Session started from $SOURCE at $TIMESTAMP" >> copilot-cli-session.log
