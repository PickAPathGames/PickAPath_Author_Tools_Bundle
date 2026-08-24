#!/bin/bash
cd "$(dirname "$0")/.."
echo "Starting Pick Engine Web Player..."
# Open the default browser
if [[ "$OSTYPE" == "darwin"* ]]; then
    open "http://localhost:8000" # Mac
else
    # xdg-open "http://localhost:8000" # Linux
    :
fi
python3 -m engine.web.server_launcher