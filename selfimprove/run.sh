#!/usr/bin/env bash
# Self-improvement loop for ONE drawing in ONE worktree.
#
#   MODELER (t2c-only build, fresh stdio t2c spawned from THIS worktree's code)
#     -> extract t2c calls  -> JUDGE (fresh, unbiased, tool-calls JSON only)
#     -> EDITOR (fix the SERVER root cause) -> rebuild on the improved code.
#
# Every below-target round goes to the EDITOR: a modeling mistake is itself evidence
# that a tool docstring misled the builder or a tool is weak — so the fix belongs in
# the server, not in patching one model. The editor decides what is actually fixable.
# Stops at accuracy>=TARGET, MAX_ITERS, or 2 consecutive NO-EDITs (no server fixes left).
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(dirname "$SELF")"; cd "$WT"
PY="$WT/mcp_server/.venv/bin/python"
CLAUDE="$(command -v claude)"

DRAWING="${1:-/Users/apple/Desktop/Assy/Assy 6.pdf}"
DRAW_DIR="$(dirname "$DRAWING")"
TARGET="${TARGET:-92}"
MAX_ITERS="${MAX_ITERS:-6}"

TS="$(date +%Y%m%d-%H%M%S)"
RUN="$SELF/runs/$TS"; mkdir -p "$RUN"
LOG="$SELF/SELFIMPROVE_LOG.md"
MODEL_TOOLS="mcp__t2c__workplane_api,mcp__t2c__sketch_api,mcp__t2c__assembly_api,mcp__t2c__extension_api,mcp__t2c__query_docs,mcp__t2c__select_model,mcp__t2c__report_learning"

# Fresh stdio t2c from THIS worktree's code+venv. Each build is an isolated process, so
# the object store is always clean AND the editor's latest commit is picked up next build.
cat > "$RUN/mcp_t2c.json" <<JSON
{"mcpServers":{"t2c":{"type":"stdio","command":"$PY","args":["$WT/mcp_server/src/t2c_mcp.py"],"env":{}}}}
JSON
echo '{"mcpServers":{}}' > "$RUN/mcp_none.json"

log(){ printf '%s\n' "$*" >> "$LOG"; }
render(){ "$PY" "$SELF/render_prompt.py" "$1"; }
sid_of(){ "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('session_id',''))" "$1" 2>/dev/null; }
# copy Claude Code's own full session transcript into the run dir for debugging
save_transcript(){ local f; f="$(find "$HOME/.claude/projects" -name "$1.jsonl" 2>/dev/null | head -1)"; [ -n "$f" ] && cp "$f" "$2" 2>/dev/null; }

RUN_START_HEAD="$(git -C "$WT" rev-parse HEAD)"
log ""; log "## Run $TS — drawing: \`$(basename "$DRAWING")\` — target $TARGET%"
echo "run dir: $RUN"

best=-1; noedit=0; outcome="max-iters"
for ((it=1; it<=MAX_ITERS; it++)); do
  echo "=== iter $it (build) ==="

  # ---- MODELER: fresh build via stdio worktree-t2c. model.$it.jsonl IS its full transcript.
  export DRAWING
  MPROMPT="$(render "$SELF/prompts/modeler.md")"
  "$CLAUDE" -p "$MPROMPT" --strict-mcp-config --mcp-config "$RUN/mcp_t2c.json" \
    --add-dir "$DRAW_DIR" --max-turns 200 \
    --permission-mode dontAsk --allowedTools "$MODEL_TOOLS" \
    --output-format stream-json --verbose < /dev/null > "$RUN/model.$it.jsonl" 2>"$RUN/model.$it.err"

  # ---- EXTRACT t2c calls (reasoning stripped -> unbiased judge input) + friction
  "$PY" "$SELF/extract_calls.py" "$RUN/model.$it.jsonl" --drawing "$DRAWING" \
    -o "$RUN/calls.$it.json" --friction-out "$RUN/friction.$it.md" 2>>"$RUN/model.$it.err"
  NCALLS="$("$PY" -c "import json;print(json.load(open('$RUN/calls.$it.json'))['n_calls'])" 2>/dev/null || echo 0)"

  # ---- JUDGE: fresh context, drawing + calls JSON only
  export CALLS_JSON="$RUN/calls.$it.json"
  JPROMPT="$(render "$SELF/prompts/judge.md")"
  "$CLAUDE" -p "$JPROMPT" --strict-mcp-config --mcp-config "$RUN/mcp_none.json" \
    --add-dir "$DRAW_DIR" --permission-mode dontAsk --output-format json \
    < /dev/null > "$RUN/judge.$it.raw.json" 2>"$RUN/judge.$it.err"
  save_transcript "$(sid_of "$RUN/judge.$it.raw.json")" "$RUN/judge.$it.transcript.jsonl"
  "$PY" "$SELF/parse_verdict.py" "$RUN/judge.$it.raw.json" "$RUN/judge.$it.json" 2>/dev/null
  ACC="$("$PY" -c "import json;print(json.load(open('$RUN/judge.$it.json')).get('accuracy',-1))" 2>/dev/null || echo -1)"
  echo "accuracy=$ACC (calls=$NCALLS)"
  log ""; log "### Iter $it — accuracy=**$ACC%** — t2c calls=$NCALLS"
  "$PY" - "$RUN/judge.$it.json" >> "$LOG" 2>/dev/null <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("- summary:", (d.get("summary","") or "").strip()[:600])
for i in d.get("issues",[]):
    print(f"  - [{i.get('severity')}/{i.get('root_cause')}] {(i.get('description') or '').strip()[:300]}")
PY

  [ "$ACC" -gt "$best" ] 2>/dev/null && best="$ACC"
  if [ "$ACC" != "-1" ] && [ "$ACC" -ge "$TARGET" ] 2>/dev/null; then outcome="reached-target"; break; fi
  [ "$it" -eq "$MAX_ITERS" ] && break

  # ---- EDITOR: always fix the server root cause. ALL judge issues + full friction
  #      history + this run's prior commits -> pick the highest-value RECURRING blocker.
  HEAD_BEFORE="$(git -C "$WT" rev-parse HEAD)"
  export ALL_ISSUES="$("$PY" - "$RUN/judge.$it.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("\n".join(f"- [{i.get('severity')}/{i.get('root_cause')}] {(i.get('description') or '').strip()}" for i in d.get("issues",[])) or "(judge listed no discrete issues)")
PY
)"
  export ALL_FRICTION="$(for f in "$RUN"/friction.*.md; do [ -f "$f" ] && { echo "===== $(basename "$f") ====="; sed -n '/## Friction/,$p' "$f"; echo; }; done)"
  export PRIOR_COMMITS="$(git -C "$WT" log --oneline "$RUN_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null)"; [ -z "$PRIOR_COMMITS" ] && export PRIOR_COMMITS="(none yet this run)"
  EPROMPT="$(render "$SELF/prompts/editor.md")"
  "$CLAUDE" -p "$EPROMPT" --add-dir "$WT" --strict-mcp-config --mcp-config "$RUN/mcp_none.json" \
    --permission-mode acceptEdits --max-turns 100 \
    --allowedTools "Bash($PY*),Bash(git add*),Bash(git commit*),Bash(git status*),Bash(git diff*),Bash(git rev-parse*),Bash(git restore*),Bash(rm *)" \
    --output-format json < /dev/null > "$RUN/editor.$it.json" 2>"$RUN/editor.$it.err"
  save_transcript "$(sid_of "$RUN/editor.$it.json")" "$RUN/editor.$it.transcript.jsonl"
  "$PY" -c "import json;print('editor:',(json.load(open('$RUN/editor.$it.json')).get('result') or '')[:400])" 2>/dev/null | tee -a "$LOG"

  if [ "$HEAD_BEFORE" != "$(git -C "$WT" rev-parse HEAD)" ]; then
    log "- **server edit committed**: \`$(git -C "$WT" rev-parse --short HEAD)\`"; noedit=0
  else
    noedit=$((noedit+1)); log "- editor made no commit (NO-EDIT ${noedit}/2)"
    [ "$noedit" -ge 2 ] && { outcome="no-more-server-fixes"; break; }
  fi
  # next iter rebuilds fresh on the (now-improved) code automatically
done

log ""; log "**Outcome: $outcome — best accuracy ${best}% after $it iteration(s). Artifacts: \`selfimprove/runs/$TS/\`**"
echo "DONE: $outcome (best=$best%). Log: $LOG  Artifacts: $RUN"
