#!/bin/bash

INPUT=$(cat)

SOURCE=$(echo "$INPUT" | jq -r '.source')

TIMESTAMP_MS=$(echo "$INPUT" | jq -r '.timestamp')
TIMESTAMP=$(date -r $((TIMESTAMP_MS / 1000)) '+%Y-%m-%d %H:%M:%S')

echo "Session started from $SOURCE at $TIMESTAMP" >> copilot-cli-session.log
