#!/usr/bin/env bash
# Self-improvement loop v2 for ONE drawing in ONE worktree. Four fresh `claude -p` agents,
# isolated context, communicating only through files in runs/<ts>/ and through git.
#
#   MODELLER (t2c only, CANNOT read src) build
#     -> extract t2c calls + friction
#   JUDGE (t2c + read src) score (geometric) + VERIFY friction -> one issue list
#   inner loop: EDITOR (code only, no t2c) fix -> VERIFIER (t2c + read src) confirm,
#               repeat until all server-fixable issues resolved or inner cap.
#   Rebuild fresh on the improved code. Stop at accuracy>=95, 5-iter stagnation, or MAX_ITERS.
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(dirname "$SELF")"; cd "$WT"                 # top-level cwd = worktree (needed for git)
PY="$WT/mcp_server/.venv/bin/python"
CLAUDE="$(command -v claude)"

DRAWING="${1:-/Users/apple/Desktop/Assy/Assy 6.pdf}"
DRAW_DIR="$(dirname "$DRAWING")"
TARGET="${TARGET:-95}"
STAGNANT="${STAGNANT:-5}"
INNER_CAP="${INNER_CAP:-4}"
MAX_ITERS="${MAX_ITERS:-12}"

TS="$(date +%Y%m%d-%H%M%S)"
RUN="$SELF/runs/$TS"; mkdir -p "$RUN/scratch"
LOG="$SELF/SELFIMPROVE_LOG.md"
T2C_TOOLS="mcp__t2c__workplane_api,mcp__t2c__sketch_api,mcp__t2c__assembly_api,mcp__t2c__extension_api,mcp__t2c__query_docs,mcp__t2c__select_model,mcp__t2c__report_learning"
EDIT_BASH="Bash($PY*),Bash(git add*),Bash(git commit*),Bash(git status*),Bash(git diff*),Bash(git rev-parse*),Bash(git log*),Bash(git restore*),Bash(rm *)"
VERIFY_TOOLS="$T2C_TOOLS,Bash(git show*),Bash(git diff*),Bash(git log*),Bash(git rev-parse*)"   # verifier: t2c + READ-ONLY git to inspect the editor's diffs

# t2c over stdio from THIS worktree's code (absolute paths -> cwd-independent). Fresh per call
# => clean store + picks up the editor's latest commit automatically.
cat > "$RUN/mcp_t2c.json" <<JSON
{"mcpServers":{"t2c":{"type":"stdio","command":"$PY","args":["$WT/mcp_server/src/t2c_mcp.py"],"env":{}}}}
JSON
echo '{"mcpServers":{}}' > "$RUN/mcp_none.json"

log(){ printf '%s\n' "$*" >> "$LOG"; }
render(){ "$PY" "$SELF/render_prompt.py" "$1"; }
sid_of(){ "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('session_id',''))" "$1" 2>/dev/null; }
save_transcript(){ local f; f="$(find "$HOME/.claude/projects" -name "$1.jsonl" 2>/dev/null | head -1)"; [ -n "$f" ] && cp "$f" "$2" 2>/dev/null; }
issues_text(){ "$PY" - "$1" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("\n".join(f"- [{i.get('severity')}/{i.get('root_cause')}] {(i.get('description') or '').strip()}" for i in d.get("issues",[])) or "(judge listed no discrete issues)")
PY
}
list_text(){ "$PY" - "$1" "$2" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("\n".join(f"- {x}" for x in d.get(sys.argv[2],[])))
PY
}

RUN_START_HEAD="$(git -C "$WT" rev-parse HEAD)"
log ""; log "## Run $TS — drawing: \`$(basename "$DRAWING")\` — target ${TARGET}%"
echo "run dir: $RUN"

best=-1; stag=0; outcome="max-iters"
for ((N=1; N<=MAX_ITERS; N++)); do
  echo "=== iter $N (build) ==="
  ITER_START_HEAD="$(git -C "$WT" rev-parse HEAD)"

  # ---- MODELLER — t2c only, cwd=scratch so it CANNOT read mcp_server/src.
  #      NEVER add `--add-dir "$WT"` here or that wall breaks.
  export DRAWING
  MPROMPT="$(render "$SELF/prompts/modeler.md")"
  ( cd "$RUN/scratch" && "$CLAUDE" -p "$MPROMPT" \
      --strict-mcp-config --mcp-config "$RUN/mcp_t2c.json" \
      --add-dir "$DRAW_DIR" --max-turns "${MODELER_TURNS:-200}" \
      --permission-mode dontAsk --allowedTools "$T2C_TOOLS" \
      --output-format stream-json --verbose < /dev/null \
      > "$RUN/modeller.$N.transcript.jsonl" 2>"$RUN/modeller.$N.err" )

  "$PY" "$SELF/extract_calls.py" "$RUN/modeller.$N.transcript.jsonl" --drawing "$DRAWING" \
    -o "$RUN/calls.$N.json" --friction-out "$RUN/friction.$N.md" 2>>"$RUN/modeller.$N.err"
  NCALLS="$("$PY" -c "import json;print(json.load(open('$RUN/calls.$N.json'))['n_calls'])" 2>/dev/null || echo 0)"

  # ---- JUDGE — t2c + read src; drawing + calls + friction; verifies friction.
  export CALLS_JSON="$RUN/calls.$N.json" FRICTION_MD="$RUN/friction.$N.md"
  JPROMPT="$(render "$SELF/prompts/judge.md")"
  "$CLAUDE" -p "$JPROMPT" --strict-mcp-config --mcp-config "$RUN/mcp_t2c.json" \
    --add-dir "$DRAW_DIR" --max-turns 120 \
    --permission-mode dontAsk --allowedTools "$T2C_TOOLS" \
    --output-format json < /dev/null > "$RUN/judge.$N.raw.json" 2>"$RUN/judge.$N.err"
  save_transcript "$(sid_of "$RUN/judge.$N.raw.json")" "$RUN/judge.$N.transcript.jsonl"
  "$PY" "$SELF/parse_verdict.py" "$RUN/judge.$N.raw.json" "$RUN/judge.$N.json" 2>/dev/null
  ACC="$("$PY" -c "import json;print(json.load(open('$RUN/judge.$N.json')).get('accuracy',-1))" 2>/dev/null || echo -1)"
  echo "accuracy=$ACC (calls=$NCALLS)"
  log ""; log "### Iter $N — accuracy=**$ACC%** — t2c calls=$NCALLS"
  "$PY" - "$RUN/judge.$N.json" >> "$LOG" 2>/dev/null <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("- summary:", (d.get("summary","") or "").strip()[:600])
for i in d.get("issues",[]):
    print(f"  - [{i.get('severity')}/{i.get('root_cause')}] {(i.get('description') or '').strip()[:300]}")
PY

  # ---- stop checks (outer)
  if [ "$ACC" -gt "$best" ] 2>/dev/null; then best="$ACC"; stag=0; else stag=$((stag+1)); fi
  if [ "$ACC" != "-1" ] && [ "$ACC" -ge "$TARGET" ] 2>/dev/null; then outcome="reached-target"; break; fi
  if [ "$stag" -ge "$STAGNANT" ]; then outcome="stagnant"; break; fi
  [ "$N" -eq "$MAX_ITERS" ] && break
  [ "$ACC" = "-1" ] && { log "- judge verdict unparseable — skipping editor this round"; continue; }

  # ---- INNER LOOP: editor <-> verifier
  ISSUES="$(issues_text "$RUN/judge.$N.json")"
  UNRESOLVED_BLOCK=""
  for ((M=1; M<=INNER_CAP; M++)); do
    HEAD_BEFORE="$(git -C "$WT" rev-parse HEAD)"
    PRIOR_COMMITS="$(git -C "$WT" log --oneline "$RUN_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null)"; [ -z "$PRIOR_COMMITS" ] && PRIOR_COMMITS="(none yet this run)"
    ITER_LABEL="$N.$M"
    export ISSUES UNRESOLVED_BLOCK PRIOR_COMMITS ITER_LABEL
    EPROMPT="$(render "$SELF/prompts/editor.md")"
    "$CLAUDE" -p "$EPROMPT" --add-dir "$WT" --strict-mcp-config --mcp-config "$RUN/mcp_none.json" \
      --permission-mode acceptEdits --max-turns 100 --allowedTools "$EDIT_BASH" \
      --output-format json < /dev/null > "$RUN/editor.$N.$M.json" 2>"$RUN/editor.$N.$M.err"
    save_transcript "$(sid_of "$RUN/editor.$N.$M.json")" "$RUN/editor.$N.$M.transcript.jsonl"
    RESULT="$("$PY" -c "import json;print((json.load(open('$RUN/editor.$N.$M.json')).get('result') or '')[:400])" 2>/dev/null)"
    echo "editor $N.$M: $RESULT" | tee -a "$LOG" >/dev/null; log "- editor $N.$M: $RESULT"

    if [ "$HEAD_BEFORE" = "$(git -C "$WT" rev-parse HEAD)" ]; then
      case "$RESULT" in
        NO-EDIT:*) log "- editor $N.$M NO-EDIT (nothing server-fixable) — ending inner loop";;
        *)         log "- editor $N.$M produced no commit — ending inner loop";;
      esac
      break
    fi
    log "- commits so far this iter: $(git -C "$WT" log --oneline "$ITER_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null | tr '\n' ' ')"

    # ---- VERIFIER — t2c + read src, fresh on the edited code
    export EDITOR_COMMITS="$(git -C "$WT" log --oneline "$ITER_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null)"
    VPROMPT="$(render "$SELF/prompts/verifier.md")"
    "$CLAUDE" -p "$VPROMPT" --add-dir "$WT" --strict-mcp-config --mcp-config "$RUN/mcp_t2c.json" \
      --add-dir "$DRAW_DIR" --max-turns 120 \
      --permission-mode dontAsk --allowedTools "$VERIFY_TOOLS" \
      --output-format json < /dev/null > "$RUN/verifier.$N.$M.raw.json" 2>"$RUN/verifier.$N.$M.err"
    save_transcript "$(sid_of "$RUN/verifier.$N.$M.raw.json")" "$RUN/verifier.$N.$M.transcript.jsonl"
    "$PY" "$SELF/parse_verifier.py" "$RUN/verifier.$N.$M.raw.json" "$RUN/verifier.$N.$M.json" 2>/dev/null
    EDITS_WORK="$("$PY" -c "import json;print(json.load(open('$RUN/verifier.$N.$M.json')).get('edits_work',False))" 2>/dev/null || echo False)"
    NUNRES="$("$PY" -c "import json;print(len(json.load(open('$RUN/verifier.$N.$M.json')).get('unresolved',[])))" 2>/dev/null || echo 99)"
    log "- verifier $N.$M: edits_work=$EDITS_WORK unresolved=$NUNRES declined=$("$PY" -c "import json;print(len(json.load(open('$RUN/verifier.$N.$M.json')).get('declined',[])))" 2>/dev/null || echo 0)"

    if [ "$EDITS_WORK" = "True" ] && [ "$NUNRES" -eq 0 ] 2>/dev/null; then
      log "- iter $N verified: edits work and all server-fixable issues resolved"; break
    fi
    UNRESOLVED_BLOCK="$(printf 'The verifier checked your previous edits on the live server and reports these STILL UNRESOLVED — address them now (fix, or justify a decline):\n%s\n' "$(list_text "$RUN/verifier.$N.$M.json" unresolved)")"
    _declined="$(list_text "$RUN/verifier.$N.$M.json" declined)"
    [ -n "$_declined" ] && UNRESOLVED_BLOCK="$UNRESOLVED_BLOCK
The verifier notes these are NOT server problems; be careful before touching them again (the verifier can be wrong — you are the final decision-maker on the code):
$_declined"
    [ "$M" -eq "$INNER_CAP" ] && log "- iter $N inner cap ($INNER_CAP) hit with $NUNRES unresolved — proceeding to next build"
  done
done

log ""; log "**Outcome: $outcome — best accuracy ${best}% after $N iteration(s). Artifacts: \`selfimprove/runs/$TS/\`**"
echo "DONE: $outcome (best=$best%). Log: $LOG  Artifacts: $RUN"
