#!/usr/bin/env bash
# Self-improvement loop for ONE drawing in ONE worktree.
#   build (modeler) -> extract calls -> judge (fresh) -> route:
#     server-limitation  -> editor edits t2c, pytest, commit -> restart server -> rebuild
#     modeling-mistake    -> modeler fixes model on the live server
#   until accuracy >= TARGET, MAX_ITERS, or a 2-round plateau.
#
# Usage: selfimprove/run.sh [DRAWING_PDF]
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(dirname "$SELF")"
PY="$WT/mcp_server/.venv/bin/python"
CLAUDE="$(command -v claude)"

DRAWING="${1:-/Users/apple/Desktop/Assy/Assy 6.pdf}"
PORT="${PORT:-9187}"
TOK="${MCP_TOKEN:-selfimprove-tok}"
SID="${SID:-assy6}"
TARGET="${TARGET:-92}"
MAX_ITERS="${MAX_ITERS:-5}"

TS="$(date +%Y%m%d-%H%M%S)"
RUN="$SELF/runs/$TS"; mkdir -p "$RUN"
LOG="$SELF/SELFIMPROVE_LOG.md"
MODEL_TOOLS="mcp__t2c__workplane_api,mcp__t2c__sketch_api,mcp__t2c__assembly_api,mcp__t2c__extension_api,mcp__t2c__query_docs,mcp__t2c__select_model,mcp__t2c__report_learning"

printf '{"mcpServers":{"t2c":{"type":"http","url":"http://127.0.0.1:%s/mcp","headers":{"Authorization":"Bearer %s","X-Session-Id":"%s"}}}}' "$PORT" "$TOK" "$SID" > "$RUN/mcp_http.json"
echo '{"mcpServers":{}}' > "$RUN/mcp_none.json"

log(){ printf '%s\n' "$*" >> "$LOG"; }
render(){ "$PY" "$SELF/render_prompt.py" "$1"; }

SRV=""
start_server(){
  MCP_TRANSPORT=http PORT="$PORT" MCP_TOKEN="$TOK" MCP_HOST=127.0.0.1 \
    "$PY" "$WT/mcp_server/src/t2c_mcp.py" > "$RUN/server.log" 2>&1 &
  SRV=$!
  for _ in $(seq 1 40); do
    curl -s -o /dev/null -m 1 -X POST "http://127.0.0.1:$PORT/mcp" \
      -H "Authorization: Bearer $TOK" -H "Accept: application/json, text/event-stream" \
      -H "Content-Type: application/json" -H "X-Session-Id: $SID" \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"0"}}}' && return 0
    sleep 0.5
  done
  echo "server failed to boot" >&2; return 1
}
stop_server(){ [ -n "$SRV" ] && kill "$SRV" 2>/dev/null; SRV=""; }
trap stop_server EXIT

log ""
log "## Run $TS — drawing: \`$(basename "$DRAWING")\` — target $TARGET%"
echo "run dir: $RUN"
start_server || exit 1
RUN_START_HEAD="$(git -C "$WT" rev-parse HEAD)"   # to list this run's own edits for the editor

mode="build"; feedback=""; best=-1; noimp=0; outcome="max-iters"
for ((it=1; it<=MAX_ITERS; it++)); do
  echo "=== iter $it ($mode) ==="

  # ---- 1. MODELER (build or fix) --------------------------------------------
  export DRAWING
  if [ "$mode" = "build" ]; then
    export FEEDBACK_BLOCK=""
  else
    export CALLS_JSON="$RUN/calls.$((it-1)).json" ISSUES="$feedback"
    export FEEDBACK_BLOCK="$(render "$SELF/prompts/feedback_block.md")"
  fi
  MPROMPT="$(render "$SELF/prompts/modeler.md")"
  "$CLAUDE" -p "$MPROMPT" --strict-mcp-config --mcp-config "$RUN/mcp_http.json" \
    --add-dir "$(dirname "$DRAWING")" --max-turns 200 \
    --permission-mode dontAsk --allowedTools "$MODEL_TOOLS" \
    --output-format stream-json --verbose < /dev/null > "$RUN/model.$it.jsonl" 2>"$RUN/model.$it.err"

  # ---- 2. EXTRACT calls + friction ------------------------------------------
  "$PY" "$SELF/extract_calls.py" "$RUN/model.$it.jsonl" --drawing "$DRAWING" \
    -o "$RUN/calls.$it.json" --friction-out "$RUN/friction.$it.md" 2>>"$RUN/model.$it.err"
  NCALLS="$("$PY" -c "import json;print(json.load(open('$RUN/calls.$it.json'))['n_calls'])" 2>/dev/null || echo 0)"

  # ---- 3. JUDGE (fresh context, tool-calls JSON only) -----------------------
  export CALLS_JSON="$RUN/calls.$it.json"
  JPROMPT="$(render "$SELF/prompts/judge.md")"
  "$CLAUDE" -p "$JPROMPT" --strict-mcp-config --mcp-config "$RUN/mcp_none.json" \
    --add-dir "$(dirname "$DRAWING")" \
    --permission-mode dontAsk --output-format json \
    < /dev/null > "$RUN/judge.$it.raw.json" 2>"$RUN/judge.$it.err"
  "$PY" "$SELF/parse_verdict.py" "$RUN/judge.$it.raw.json" "$RUN/judge.$it.json" 2>/dev/null

  ACC="$("$PY" -c "import json;print(json.load(open('$RUN/judge.$it.json')).get('accuracy',-1))" 2>/dev/null || echo -1)"
  echo "accuracy=$ACC (calls=$NCALLS)"
  log ""
  log "### Iter $it — mode=$mode — accuracy=**$ACC%** — t2c calls=$NCALLS"
  "$PY" - "$RUN/judge.$it.json" >> "$LOG" 2>/dev/null <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("- summary:", d.get("summary","").strip()[:600])
for i in d.get("issues",[]):
    print(f"  - [{i.get('severity')}/{i.get('root_cause')}] {i.get('description','').strip()[:300]}")
PY

  # ---- 4. STOP? -------------------------------------------------------------
  if [ "$ACC" != "-1" ] && [ "$ACC" -ge "$TARGET" ] 2>/dev/null; then outcome="reached-target"; break; fi
  if [ "$ACC" -le "$best" ] 2>/dev/null; then noimp=$((noimp+1)); else best="$ACC"; noimp=0; fi
  if [ "$noimp" -ge 2 ]; then outcome="plateau"; break; fi
  [ "$it" -eq "$MAX_ITERS" ] && break

  # ---- 5. ROUTE: server-limitation vs modeling mistake ----------------------
  SERVER_ISSUES="$("$PY" - "$RUN/judge.$it.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
s=[i for i in d.get("issues",[]) if i.get("root_cause")=="server-limitation"]
print("\n".join(f"- [{i.get('severity')}] {i.get('description','').strip()}" for i in s))
PY
)"
  MODEL_ISSUES="$("$PY" - "$RUN/judge.$it.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
s=[i for i in d.get("issues",[]) if i.get("root_cause")!="server-limitation"]
print("\n".join(f"- [{i.get('severity')}] {i.get('description','').strip()}" for i in s))
PY
)"
  FR="$(sed -n '/## Friction/,$p' "$RUN/friction.$it.md" 2>/dev/null | sed '1d' | sed '/^[[:space:]]*$/d')"
  FR_SUBSTANTIVE=0
  [ -n "$FR" ] && ! printf '%s' "$FR" | grep -qiE '^[[:space:]]*none\.?[[:space:]]*$' && FR_SUBSTANTIVE=1

  if [ -n "$SERVER_ISSUES" ] || [ "$FR_SUBSTANTIVE" -eq 1 ]; then
    echo "-> server-edit round"
    HEAD_BEFORE="$(git -C "$WT" rev-parse HEAD)"
    export SERVER_ISSUES
    export ALL_FRICTION="$(for f in "$RUN"/friction.*.md; do [ -f "$f" ] && { echo "===== $(basename "$f") ====="; sed -n '/## Friction/,$p' "$f"; echo; }; done)"
    export PRIOR_COMMITS="$(git -C "$WT" log --oneline "$RUN_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null)"; [ -z "$PRIOR_COMMITS" ] && export PRIOR_COMMITS="(none yet this run)"
    EPROMPT="$(render "$SELF/prompts/editor.md")"
    stop_server
    "$CLAUDE" -p "$EPROMPT" --add-dir "$WT" --strict-mcp-config --mcp-config "$RUN/mcp_none.json" \
      --permission-mode acceptEdits --max-turns 80 \
      --allowedTools "Bash($PY -m pytest*),Bash(git add*),Bash(git commit*),Bash(git status*),Bash(git diff*),Bash(git rev-parse*),Bash(git restore*)" \
      --output-format json < /dev/null > "$RUN/editor.$it.json" 2>"$RUN/editor.$it.err"
    "$PY" -c "import json;print('editor:',(json.load(open('$RUN/editor.$it.json')).get('result') or '')[:300])" 2>/dev/null | tee -a "$LOG"
    HEAD_AFTER="$(git -C "$WT" rev-parse HEAD)"
    if [ "$HEAD_BEFORE" != "$HEAD_AFTER" ]; then
      log "- **server edit committed**: \`$HEAD_AFTER\` (was \`$HEAD_BEFORE\`)"
      start_server || { outcome="server-edit-broke-boot"; break; }
      mode="build"; feedback=""        # rebuild fresh = verify the edit
      continue
    fi
    log "- editor made no commit (NO-EDIT) — falling back to model-fix"
    start_server || { outcome="server-restart-failed"; break; }
  fi

  # ---- modeling mistake path (or editor no-op): fix on the live model -------
  mode="fix"; feedback="$MODEL_ISSUES"
done

stop_server
log ""
log "**Outcome: $outcome — best accuracy ${best}% after $it iteration(s). Artifacts: \`selfimprove/runs/$TS/\`**"
echo "DONE: $outcome (best=$best%). Log: $LOG  Artifacts: $RUN"
