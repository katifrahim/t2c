#!/usr/bin/env bash
# Self-improvement loop for ONE drawing in ONE worktree. Four fresh `claude -p` agents,
# isolated context, communicating only through files in runs/<ts>/ and through git.
#   ① MODELLER (t2c, prod-like) build → extract calls + friction → export CAD.N.step
#   ② JUDGE (t2c + docs + read src) score (geometric) + verify friction → one issue list
#   inner loop: ③ EDITOR (t2c repro + code edit + docs) fix → ④ VERIFIER (t2c + read src + git) confirm
#   Rebuild fresh on improved code. Stop at accuracy>=95, 5-iter stagnation, or MAX_ITERS.
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WT="$(dirname "$SELF")"; cd "$WT"                       # top-level cwd = worktree (git + source reads)
PY="$WT/mcp_server/.venv/bin/python"
CLAUDE="$(command -v claude)"

# --- HARD guard: agents MUST run on the patched claude (full, untruncated MCP tool docstrings).
case "$(readlink -f "$CLAUDE" 2>/dev/null)" in
  *claude-patched) : ;;
  *) echo "run.sh STOPPED: 'claude' ($CLAUDE) is not the patched binary."
     echo "Spawned agents would get t2c tool docstrings truncated to 2KB and build/judge/fix badly."
     echo "Repin claude to the patched binary with full untruncated MCP tool docstrings:"
     echo "  ln -sf ~/.claude/patches/claude-patched ~/.local/bin/claude"
     exit 1 ;;
esac
export ENABLE_TOOL_SEARCH=0                             # load all t2c schemas up front, untruncated

# --- secrets: CONTEXT7_API_KEY lives in the (git-ignored) .env at the worktree root --------------
[ -f "$WT/.env" ] && set -a && . "$WT/.env" && set +a

# --- thinking / effort --------------------------------------------------------------------------
# Opus 5 is ADAPTIVE-THINKING-ONLY: there is no thinking budget to set, only `effort` (soft guidance)
# and max_tokens (a hard ceiling on thinking + response text combined). An 11-condition matrix on the
# real modeller task found that peak thinking-tokens-in-one-turn separates success from failure with
# NO overlap:   built (7 runs): 24,050-56,500 peak   ·   built nothing (4 runs): 63,950-68,700 peak
# Effort is the dominant lever: same prompt, same cap, high -> 0 build calls, medium -> 40.
EFFORT="${EFFORT:-medium}"              # low|medium|high|xhigh|max
#
# DELIBERATELY NOT SET (previously `export CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000`): raising the ceiling
# was a symptomatic fix for over-thinking. It let a runaway planning turn burn silently for 25 min
# instead of failing fast, and at medium effort the DEFAULT cap beat 128K (40 vs 20 build calls) while
# sitting further below the ~60K cliff. Leave it unset unless you have measured a reason not to.
# export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-128000}"
#
# ALSO DELIBERATELY NOT SET: MAX_THINKING_TOKENS. Claude Code maps it to the legacy
# thinking:{type:"enabled",budget_tokens:N} API which Opus 5 does not support; verified ignored
# (a 10,000 budget still peaked at 66,350 thinking tokens and built nothing).

# --- the drawing: a FOLDER of page images (PNG/JPG), one image per sheet. No PDFs. ---------------
DRAWING="${1:-/Users/apple/Desktop/assy/Assy 6}"
[ -d "$DRAWING" ] || { echo "run.sh STOPPED: '$DRAWING' is not a directory."
  echo "  The loop takes a FOLDER of page images, e.g. '/Users/apple/Desktop/assy/Assy 6'."; exit 1; }
DRAW_DIR="$DRAWING"                                     # agents get read access to the folder itself
DRAWING_IMAGES="$(find "$DRAWING" -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) | sort -V | sed 's/^/    /')"
[ -n "$DRAWING_IMAGES" ] || { echo "run.sh STOPPED: no .png/.jpg sheets in '$DRAWING'."; exit 1; }
N_SHEETS="$(printf '%s\n' "$DRAWING_IMAGES" | grep -c .)"
DRAWING_NAME="$(basename "$DRAWING")"
export DRAWING_IMAGES N_SHEETS DRAWING_NAME
TARGET="${TARGET:-95}"; STAGNANT="${STAGNANT:-4}"; INNER_CAP="${INNER_CAP:-4}"; MAX_ITERS="${MAX_ITERS:-12}"
# Opus 5 for the two agents that READ THE DRAWING IMAGES (it is markedly better at images);
# Opus 4.8 for the two text-only agents, which cuts usage substantially. The `opus` alias is stale
# on this binary (resolves to 4.8), so both ids are explicit.
MODEL_VISION="${MODEL_VISION:-claude-opus-5[1m]}"   # modeller, judge  — read the sheet images
MODEL_TEXT="${MODEL_TEXT:-claude-opus-4-8}"         # editor, verifier — text/code only

TS="$(date +%Y%m%d-%H%M%S)"; RUN="$SELF/runs/$TS"; mkdir -p "$RUN/scratch"
LOG="$SELF/SELFIMPROVE_LOG.md"

# --- tool sets ---------------------------------------------------------------------------------
T2C_ALL="mcp__t2c__workplane_api,mcp__t2c__sketch_api,mcp__t2c__assembly_api,mcp__t2c__extension_api,mcp__t2c__select_model,mcp__t2c__inspect_model,mcp__t2c__edit_model,mcp__t2c__query_docs,mcp__t2c__report_learning"
T2C_NR="mcp__t2c__workplane_api,mcp__t2c__sketch_api,mcp__t2c__assembly_api,mcp__t2c__extension_api,mcp__t2c__select_model,mcp__t2c__inspect_model,mcp__t2c__edit_model,mcp__t2c__query_docs"   # no report_learning
CTX7="mcp__context7__resolve-library-id,mcp__context7__query-docs"
GIT_RO="Bash(git show*),Bash(git diff*),Bash(git log*),Bash(git rev-parse*)"
EDIT_BASH="Bash($PY*),Bash(git add*),Bash(git commit*),Bash(git status*),Bash(git diff*),Bash(git rev-parse*),Bash(git log*),Bash(git restore*),Bash(rm *)"
MODELER_TOOLS="$T2C_ALL"                                # modeller mirrors prod (keeps report_learning)
JUDGE_TOOLS="$T2C_NR,$CTX7,WebSearch"
VERIFY_TOOLS="$T2C_NR,$CTX7,WebSearch,$GIT_RO"
EDITOR_TOOLS="$T2C_NR,$CTX7,WebSearch,$EDIT_BASH"

# --- mcp configs -------------------------------------------------------------------------------
cat > "$RUN/mcp_t2c.json" <<JSON
{"mcpServers":{"t2c":{"type":"stdio","command":"$PY","args":["$WT/mcp_server/src/t2c_mcp.py"],"env":{}}}}
JSON
CTX7_HDR=""; [ -n "${CONTEXT7_API_KEY:-}" ] && CTX7_HDR=",\"headers\":{\"Authorization\":\"Bearer $CONTEXT7_API_KEY\"}"
cat > "$RUN/mcp_t2c_ctx7.json" <<JSON
{"mcpServers":{"t2c":{"type":"stdio","command":"$PY","args":["$WT/mcp_server/src/t2c_mcp.py"],"env":{}},"context7":{"type":"http","url":"https://mcp.context7.com/mcp"$CTX7_HDR}}}
JSON

# --- helpers -----------------------------------------------------------------------------------
render(){ "$PY" "$SELF/render_prompt.py" "$1"; }
sid_of(){ "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('session_id',''))" "$1" 2>/dev/null; }
save_transcript(){ local f; f="$(find "$HOME/.claude/projects" -name "$1.jsonl" 2>/dev/null | head -1)"; [ -n "$f" ] && cp "$f" "$2" 2>/dev/null; }
logw(){ "$PY" "$SELF/log_writer.py" "$LOG" "$@"; }
jget(){ "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2], sys.argv[3]))" "$1" "$2" "${3:-}" 2>/dev/null; }
jlen(){ "$PY" -c "import json,sys;v=json.load(open(sys.argv[1])).get(sys.argv[2]);print(len(v) if isinstance(v,list) else 0)" "$1" "$2" 2>/dev/null || echo 0; }
issues_text(){ "$PY" - "$1" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("\n".join(f"- [{i.get('severity')}] {(i.get('description') or '').strip()}" for i in d.get("issues",[])) or "(no issues)")
PY
}
list_text(){ "$PY" -c "import json,sys;print(chr(10).join('- '+str(x) for x in json.load(open(sys.argv[1])).get(sys.argv[2],[])))" "$1" "$2" 2>/dev/null; }

# Stop the whole run once the Anthropic account is out of usage: every later agent just returns the
# same "spend limit" text, so the loop would otherwise grind through its remaining iterations
# producing empty artifacts and finish by reporting a best accuracy that means nothing.
ABORT=""
# Only the tail of a stream-json transcript is scanned: the terminal result record is the last
# line, and a 5MB build transcript could otherwise match the phrase in ordinary content.
limit_hit(){ { case "$1" in *.jsonl) tail -c 4000 "$1" 2>/dev/null;; *) cat "$1" 2>/dev/null;; esac; } \
  | grep -qiE "spend limit|usage limit|usage-credits|exceed your account" && ABORT="spend-limit"; }

RUN_START_HEAD="$(git -C "$WT" rev-parse HEAD)"
{ echo ""; echo "## Run $TS — drawing: \`$DRAWING_NAME\` ($N_SHEETS sheets) — effort ${EFFORT} — target ${TARGET}%"; } >> "$LOG"
echo "run dir: $RUN"

best=-1; stag=0; outcome="max-iters"
for ((N=1; N<=MAX_ITERS; N++)); do
  echo "=== outer iter $N (build) ==="
  ITER_START_HEAD="$(git -C "$WT" rev-parse HEAD)"
  logw outer_header "$N"

  # ---- ① MODELLER — prod-like, t2c only; cwd=scratch so it CANNOT read mcp_server/src.
  #      NEVER add `--add-dir "$WT"` here or that wall breaks.
  export DRAWING; MPROMPT="$(render "$SELF/prompts/modeler.md")"
  ( cd "$RUN/scratch" && "$CLAUDE" -p "$MPROMPT" --model "$MODEL_VISION" --effort "$EFFORT" \
      --strict-mcp-config --mcp-config "$RUN/mcp_t2c.json" --add-dir "$DRAW_DIR" \
      --permission-mode dontAsk --allowedTools "$MODELER_TOOLS" \
      --output-format stream-json --verbose < /dev/null \
      > "$RUN/modeller.$N.transcript.jsonl" 2>"$RUN/modeller.$N.err" )
  limit_hit "$RUN/modeller.$N.transcript.jsonl"
  [ -n "$ABORT" ] && break
  "$PY" "$SELF/extract_calls.py" "$RUN/modeller.$N.transcript.jsonl" --drawing "$DRAWING" \
    -o "$RUN/calls.$N.json" --friction-out "$RUN/friction.$N.json" 2>>"$RUN/modeller.$N.err"
  "$PY" "$SELF/export_model.py" "$RUN/calls.$N.json" "$RUN/CAD.$N.step" "$RUN/geometry.$N.json" 2>>"$RUN/modeller.$N.err" || true
  logw modeller "$N" "$RUN/calls.$N.json" "$RUN/friction.$N.json" "$RUN/modeller.$N.transcript.jsonl" "$RUN/CAD.$N.step"

  # ---- ② JUDGE — t2c + docs + read src; scores geometry, verifies friction → one issue list
  export CALLS_JSON="$RUN/calls.$N.json" FRICTION_MD="$RUN/friction.$N.json" GEOMETRY_JSON="$RUN/geometry.$N.json"
  "$CLAUDE" -p "$(render "$SELF/prompts/judge.md")" --model "$MODEL_VISION" --effort "$EFFORT" \
    --strict-mcp-config --mcp-config "$RUN/mcp_t2c_ctx7.json" --add-dir "$DRAW_DIR" \
    --permission-mode dontAsk --allowedTools "$JUDGE_TOOLS" \
    --output-format json < /dev/null > "$RUN/judge.$N.raw.json" 2>"$RUN/judge.$N.err"
  save_transcript "$(sid_of "$RUN/judge.$N.raw.json")" "$RUN/judge.$N.transcript.jsonl"
  "$PY" "$SELF/parse_json_block.py" "$RUN/judge.$N.raw.json" "$RUN/judge.$N.json" >/dev/null 2>&1
  limit_hit "$RUN/judge.$N.raw.json"
  [ -n "$ABORT" ] && break
  ACC="$(jget "$RUN/judge.$N.json" accuracy -1)"
  logw judge "$N" "$RUN/judge.$N.transcript.jsonl" "$RUN/judge.$N.json"
  echo "accuracy=$ACC"

  # ---- stop checks (outer)
  if [ "$ACC" -gt "$best" ] 2>/dev/null; then best="$ACC"; stag=0; else stag=$((stag+1)); fi
  if [ "$ACC" != "-1" ] && [ "$ACC" -ge "$TARGET" ] 2>/dev/null; then outcome="reached-target"; break; fi
  if [ "$stag" -ge "$STAGNANT" ]; then outcome="stagnant"; break; fi
  [ "$N" -eq "$MAX_ITERS" ] && break
  [ "$ACC" = "-1" ] && continue                          # judge verdict unparseable → skip editor

  # ---- INNER LOOP: editor ↔ verifier
  NISS="$(jlen "$RUN/judge.$N.json" issues)"
  ISSUES="$(issues_text "$RUN/judge.$N.json")"; UNRESOLVED_BLOCK=""; NUNRES=0
  for ((M=1; M<=INNER_CAP; M++)); do
    HEAD_BEFORE="$(git -C "$WT" rev-parse HEAD)"
    PRIOR_COMMITS="$(git -C "$WT" log --oneline "$RUN_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null)"; [ -z "$PRIOR_COMMITS" ] && PRIOR_COMMITS="(none yet this run)"
    ITER_LABEL="$N.$M"
    export ISSUES UNRESOLVED_BLOCK PRIOR_COMMITS ITER_LABEL
    logw inner_header "$N" "$M"

    # ③ EDITOR — t2c repro + code edit + docs (no report_learning)
    "$CLAUDE" -p "$(render "$SELF/prompts/editor.md")" --model "$MODEL_TEXT" --effort "$EFFORT" \
      --strict-mcp-config --mcp-config "$RUN/mcp_t2c_ctx7.json" --add-dir "$WT" \
      --permission-mode acceptEdits --allowedTools "$EDITOR_TOOLS" \
      --output-format json < /dev/null > "$RUN/editor.$N.$M.raw.json" 2>"$RUN/editor.$N.$M.err"
    save_transcript "$(sid_of "$RUN/editor.$N.$M.raw.json")" "$RUN/editor.$N.$M.transcript.jsonl"
    "$PY" "$SELF/parse_json_block.py" "$RUN/editor.$N.$M.raw.json" "$RUN/editor.$N.$M.json" >/dev/null 2>&1
    limit_hit "$RUN/editor.$N.$M.raw.json"
    [ -n "$ABORT" ] && break
    HEAD_AFTER="$(git -C "$WT" rev-parse HEAD)"
    EDITOR_COMMITS="$(git -C "$WT" log --oneline "$ITER_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null)"
    logw editor "$N" "$M" "$RUN/editor.$N.$M.transcript.jsonl" "$RUN/editor.$N.$M.json" "$NISS" "$NUNRES" "$(git -C "$WT" log --oneline "$HEAD_BEFORE"..HEAD -- mcp_server/ 2>/dev/null)"

    if [ "$HEAD_BEFORE" = "$HEAD_AFTER" ]; then
      # An editor that edited but never committed did NOT complete its task, and its changes stay
      # live in the tree — silently altering the server for later iterations with no commit to
      # show for it. Say so loudly instead of logging a bare "no commit".
      if [ -n "$(git -C "$WT" status --porcelain -- mcp_server/ 2>/dev/null)" ]; then
        logw note "editor $N.$M left UNCOMMITTED changes under mcp_server/ — they are live for later iterations but recorded by no commit; review \`git status\` before trusting anything downstream"
      else
        logw note "editor $N.$M made no commit and changed nothing — ending inner loop"
      fi
      break
    fi

    # ④ VERIFIER — t2c (fresh, edited code) + read src + read-only git
    export EDITOR_COMMITS
    export VERIFY_CHECKLIST="$(list_text "$RUN/editor.$N.$M.json" verify)"; [ -z "$VERIFY_CHECKLIST" ] && export VERIFY_CHECKLIST="(the author listed no specific checks)"
    "$CLAUDE" -p "$(render "$SELF/prompts/verifier.md")" --model "$MODEL_TEXT" --effort "$EFFORT" \
      --strict-mcp-config --mcp-config "$RUN/mcp_t2c_ctx7.json" --add-dir "$WT" --add-dir "$DRAW_DIR" \
      --permission-mode dontAsk --allowedTools "$VERIFY_TOOLS" \
      --output-format json < /dev/null > "$RUN/verifier.$N.$M.raw.json" 2>"$RUN/verifier.$N.$M.err"
    save_transcript "$(sid_of "$RUN/verifier.$N.$M.raw.json")" "$RUN/verifier.$N.$M.transcript.jsonl"
    "$PY" "$SELF/parse_json_block.py" "$RUN/verifier.$N.$M.raw.json" "$RUN/verifier.$N.$M.json" >/dev/null 2>&1
    NCOMMITS="$(git -C "$WT" rev-list --count "$ITER_START_HEAD"..HEAD -- mcp_server/ 2>/dev/null || echo 0)"
    logw verifier "$N" "$M" "$RUN/verifier.$N.$M.transcript.jsonl" "$RUN/verifier.$N.$M.json" "$NISS" "$NCOMMITS"

    limit_hit "$RUN/verifier.$N.$M.raw.json"
    [ -n "$ABORT" ] && break
    EDITS_WORK="$(jget "$RUN/verifier.$N.$M.json" edits_work False)"
    NUNRES="$(jlen "$RUN/verifier.$N.$M.json" unresolved)"
    if [ "$EDITS_WORK" = "True" ] && [ "$NUNRES" -eq 0 ] 2>/dev/null; then break; fi
    UNRESOLVED_BLOCK="$(printf 'A re-check on the current server reports these STILL UNRESOLVED — address them now (fix, or justify why not a server problem):\n%s\n' "$(list_text "$RUN/verifier.$N.$M.json" unresolved)")"
    _dec="$(list_text "$RUN/verifier.$N.$M.json" declined)"
    [ -n "$_dec" ] && UNRESOLVED_BLOCK="$UNRESOLVED_BLOCK
The re-check considers these NOT server problems; be careful before touching them again (it can be wrong — you decide):
$_dec"
  done
  [ -n "$ABORT" ] && { outcome="$ABORT"; break; }
done

[ -n "$ABORT" ] && { outcome="$ABORT"; logw note "run stopped early: Anthropic usage/spend limit reached"; }
logw outcome "$outcome" "$best" "$N"
echo "DONE: $outcome (best=$best%). Log: $LOG  Artifacts: $RUN"
