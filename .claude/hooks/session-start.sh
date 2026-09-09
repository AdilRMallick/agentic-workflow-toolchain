#!/bin/bash
# SessionStart hook: make the repo immediately usable in a fresh container.
#
# Installs the package in editable mode with dev extras so `pytest`, `ruff`,
# `mypy` and the `awt` CLI all work, then reports the current tracker state so
# the session starts knowing what is being tracked.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}"

if command -v uv >/dev/null 2>&1; then
  uv pip install --system -e '.[dev]' >/dev/null
else
  python3 -m pip install --quiet --upgrade pip >/dev/null
  python3 -m pip install --quiet -e '.[dev]' >/dev/null
fi

# The MCP servers and the CLI must agree on one database for the session.
echo 'export AWT_DB="${CLAUDE_PROJECT_DIR:-.}/.awt/toolchain.db"' >> "${CLAUDE_ENV_FILE:-/dev/null}"

export AWT_DB="${AWT_DB:-.awt/toolchain.db}"
awt sync config/trackers.example.toml >/dev/null 2>&1 || true

echo "agentic-workflow-toolchain ready — $(awt --version)"
awt list 2>/dev/null || true
