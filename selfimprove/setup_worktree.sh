#!/usr/bin/env bash
# Create an isolated venv for THIS worktree that shares main's heavy deps
# (cadquery/OCP/vtk) but registers its OWN editable `src` -> this worktree,
# so server + pytest run the worktree's t2c code, not main's.
#
# Usage: selfimprove/setup_worktree.sh [/path/to/main/checkout]
set -euo pipefail
MAIN="${1:-/Users/apple/Desktop/t2c}"
WT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MAIN_PY="$MAIN/mcp_server/.venv/bin/python"
[ -x "$MAIN_PY" ] || { echo "main venv python not found at $MAIN_PY" >&2; exit 1; }
MAIN_SP="$("$MAIN_PY" -c 'import site;print(site.getsitepackages()[0])')"

echo "worktree: $WT"
echo "main deps: $MAIN_SP"
rm -rf "$WT/mcp_server/.venv"
"$MAIN_PY" -m venv "$WT/mcp_server/.venv"           # same 3.13 ABI as main
WT_PY="$WT/mcp_server/.venv/bin/python"
WT_SP="$("$WT_PY" -c 'import site;print(site.getsitepackages()[0])')"
printf '%s\n' "$MAIN_SP" > "$WT_SP/_main_deps.pth"  # share heavy deps (path only; not main's .pth)
"$WT/mcp_server/.venv/bin/pip" install -e "$WT/mcp_server" --no-deps -q

"$WT_PY" - <<'PY'
import importlib.util as u
loc = u.find_spec("src").submodule_search_locations[0]
assert "t2c-assy6" in loc or "/mcp_server/src" in loc, loc
import cadquery, OCP.OCP, vtk  # shared heavy deps must import
print("worktree venv OK -> src at", loc)
PY
