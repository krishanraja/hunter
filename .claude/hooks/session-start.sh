#!/bin/bash
set -euo pipefail

# Only Claude Code on the web containers need this; local checkouts manage
# their own environment. The scheduled Routine's fresh sessions land here.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Editable install with dev extras: runtime deps for python -m hunter.run,
# pytest for the offline suite. Idempotent; pip no-ops what is satisfied.
pip install -q -e ".[dev]"

echo "hunter: dependencies installed"
