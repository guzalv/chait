#!/usr/bin/env bash
# chait launch — spawn a team of AI agents collaborating on a task.
#
# Usage:
#   ./launch.sh --task "implement rate limiting"       # auto-creates room (needs CHAIT_TOKEN)
#   ./launch.sh --task "..." --room my-room            # custom room name
#   ./launch.sh --token <join-token> --task "..."      # existing room (no CHAIT_TOKEN needed)
#   ./launch.sh --task "..." --team "pm,lead,dev"
#   ./launch.sh --task "..." --team "pm:Alice,lead:Bob,dev:Carol"
#   ./launch.sh --task "..." --context "~/sw/myproject"
#
# Environment:
#   CHAIT_TOKEN        API token for creating rooms (generate in chait web UI)
#   CHAIT_SERVER       Server URL (default: http://localhost:3100)
#
# Options:
#   --token TOKEN      Join token for an existing room (skips room creation)
#   --room NAME        Room name when auto-creating (default: auto-generated)
#   --task TEXT         Task description (prompted if omitted)
#   --task-file FILE   Read task description from file (for long/multiline tasks)
#   --server URL       Chait server URL (default: $CHAIT_SERVER or http://localhost:3100)
#   --team SPEC        Comma-separated roles or role:name pairs
#                      (default: pm,lead,principal,senior)
#   --context PATH     Working directory for agents (default: cwd)
#   --model MODEL      LLM model for agents (default: sonnet)
#   --runner CMD       Agent runner: "opencode" or "claude" (default: auto-detect)
#   --dry-run          Print prompts without spawning agents
#   -h, --help         Show this help

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SERVER="${CHAIT_SERVER:-http://localhost:3100}"
JOIN_TOKEN=""
ROOM_NAME=""
TEAM_SPEC=""
TASK=""
CONTEXT="$(pwd)"
MODEL="sonnet"
RUNNER=""
DRY_RUN=false

# ---------------------------------------------------------------------------
# Default team roles
# ---------------------------------------------------------------------------
declare -A ROLE_DEFAULTS=(
  [pm]="Product Manager"
  [lead]="Tech Lead"
  [principal]="Principal Engineer"
  [senior]="Senior Engineer"
)

declare -A ROLE_DESCRIPTIONS=(
  [pm]="You are the Product Manager. You co-own the conversation with the Tech Lead. Your job: define requirements, acceptance criteria, and priorities. Validate that the technical approach meets user needs. Push back on scope creep. You do NOT write code. At the start, work with the Tech Lead to clarify the task, break it down, and agree on requirements before instructing others to act."
  [lead]="You are the Tech Lead / Architect. You co-own the conversation with the PM. Your job: drive the technical direction, make architecture decisions, review proposals. At the start, work with the PM to clarify requirements and break down the task. Once aligned, assign specific work to engineers via the chat. You delegate — do not implement everything yourself."
  [principal]="You are a Principal Engineer. Wait for instructions from the Tech Lead or PM before acting. When assigned work: implement the core pieces, review others' work for correctness and edge cases, write production-quality code. Challenge design decisions when you see real problems — but do not act without being asked."
  [senior]="You are a Senior Engineer. Wait for instructions from the Tech Lead or PM before acting. When assigned work: implement features, write thorough tests (unit + integration), handle edge cases. Ask clarifying questions when requirements are ambiguous — but do not act without being asked."
)

declare -A ROLE_CARDS=(
  [pm]='{"description":"Product manager — requirements, priorities, user impact","skills":["requirements","prioritization","acceptance criteria","stakeholder communication"],"tools":["curl"]}'
  [lead]='{"description":"Tech lead / architect — design, code review, technical decisions","skills":["system design","architecture","code review","Go","gRPC","SQL"],"tools":["curl","go","make"]}'
  [principal]='{"description":"Principal engineer — implementation, deep review, mentoring","skills":["Go","testing","performance","security","system internals"],"tools":["curl","go","make","git"]}'
  [senior]='{"description":"Senior engineer — implementation, testing, documentation","skills":["Go","testing","API development","SQL","CI/CD"],"tools":["curl","go","make","git"]}'
)

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --token)    JOIN_TOKEN="$2"; shift 2 ;;
    --room)     ROOM_NAME="$2"; shift 2 ;;
    --task)     TASK="$2"; shift 2 ;;
    --task-file) TASK="$(cat "$2")"; shift 2 ;;
    --server)   SERVER="$2"; shift 2 ;;
    --team)     TEAM_SPEC="$2"; shift 2 ;;
    --context)  CONTEXT="$2"; shift 2 ;;
    --model)    MODEL="$2"; shift 2 ;;
    --runner)   RUNNER="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    -h|--help)
      sed -n '2,/^$/{ s/^# //; s/^#//; p }' "$0"
      exit 0 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Interactive prompts for missing values
# ---------------------------------------------------------------------------
if [[ -z "$JOIN_TOKEN" && -n "${CHAIT_TOKEN:-}" ]]; then
  # Auto-create room via API using master token
  if [[ -z "$ROOM_NAME" ]]; then
    ROOM_NAME="room-$(date +%Y%m%d-%H%M%S)"
  fi
  # Collect task first so we can use it as the room topic
  if [[ -z "$TASK" ]]; then
    echo "Task description (paste multiline text, then press Ctrl+D when done):"
    TASK="$(cat)"
    TASK="$(echo "$TASK" | sed '/^$/d')"
    [[ -z "$TASK" ]] && { echo "Task required."; exit 1; }
  fi
  ROOM_RESP=$(curl -sf -X POST "$SERVER/api/v1/rooms" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $CHAIT_TOKEN" \
    -d "{\"name\": \"$ROOM_NAME\", \"topic\": $(echo "$TASK" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}" 2>/dev/null) || {
    echo "Error: failed to create room (server: $SERVER). Is CHAIT_TOKEN correct?"
    exit 1
  }
  JOIN_TOKEN=$(echo "$ROOM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['join_token'])")
  ROOM=$(echo "$ROOM_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['name'])")
  echo "Created room: $ROOM"
elif [[ -z "$JOIN_TOKEN" ]]; then
  echo "=== chait — team launcher ==="
  echo ""
  echo "Tip: set CHAIT_TOKEN env var to skip this step (auto-creates rooms)."
  echo "Otherwise, create a room in the chait web UI and paste the join token."
  read -rp "Join token: " JOIN_TOKEN
  [[ -z "$JOIN_TOKEN" ]] && { echo "Join token required. Set CHAIT_TOKEN or create a room in the UI."; exit 1; }
fi

if [[ -z "${ROOM:-}" ]]; then
  # Resolve room name from join token
  ROOM_INFO=$(curl -sf -X POST "$SERVER/api/v1/join" \
    -H "Content-Type: application/json" \
    -d "{\"join_token\": \"$JOIN_TOKEN\", \"name\": \"__probe__\", \"role\": \"probe\"}" 2>/dev/null) || {
    echo "Error: invalid join token or server unreachable ($SERVER)"
    exit 1
  }
  ROOM=$(echo "$ROOM_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['room'])")
  # Delete the probe agent (it registered but we don't need it)
  # No delete endpoint, but it's harmless — just a DB row
fi

if [[ -z "$TASK" ]]; then
  echo ""
  echo "Task description (paste multiline text, then press Ctrl+D when done):"
  TASK="$(cat)"
  TASK="$(echo "$TASK" | sed '/^$/d')"
  [[ -z "$TASK" ]] && { echo "Task required."; exit 1; }
fi

if [[ -z "$TEAM_SPEC" ]]; then
  echo ""
  echo "Team composition (comma-separated roles, or role:name)."
  echo "Available roles: pm, lead, principal, senior"
  echo "Default: pm,lead,principal,senior"
  echo ""
  read -rp "Team [pm,lead,principal,senior]: " TEAM_SPEC
  [[ -z "$TEAM_SPEC" ]] && TEAM_SPEC="pm,lead,principal,senior"
fi

# ---------------------------------------------------------------------------
# Parse team spec into arrays
# ---------------------------------------------------------------------------
declare -a ROLES=()
declare -a NAMES=()

IFS=',' read -ra MEMBERS <<< "$TEAM_SPEC"
for member in "${MEMBERS[@]}"; do
  member=$(echo "$member" | xargs)  # trim
  if [[ "$member" == *:* ]]; then
    role="${member%%:*}"
    name="${member#*:}"
  else
    role="$member"
    name="${ROLE_DEFAULTS[$role]:-Agent-$role}"
  fi
  ROLES+=("$role")
  NAMES+=("$name")
done

echo ""
echo "Task:    $TASK"
echo "Room:    $ROOM"
echo "Server:  $SERVER"
echo "Context: $CONTEXT"
echo "Model:   $MODEL"
echo "Team:    ${ROLES[*]}"
echo ""

# ---------------------------------------------------------------------------
# Register agents using join token
# ---------------------------------------------------------------------------
echo "Registering agents..."

declare -a TOKENS=()
declare -a IDS=()

for i in "${!ROLES[@]}"; do
  role="${ROLES[$i]}"
  name="${NAMES[$i]}"
  card="${ROLE_CARDS[$role]:-'{}'}"

  result=$(curl -sf -X POST "$SERVER/api/v1/join" \
    -H "Content-Type: application/json" \
    -d "{\"join_token\": \"$JOIN_TOKEN\", \"name\": \"$name\", \"role\": \"$role\", \"card\": $card}")

  token=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['agent_token'])")
  agent_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  TOKENS+=("$token")
  IDS+=("$agent_id")
  echo "  Registered: $name ($role) -> $agent_id"
done

# Build team roster for prompts
ROSTER=""
for i in "${!ROLES[@]}"; do
  ROSTER+="  - ${NAMES[$i]} (${ROLES[$i]}, id: ${IDS[$i]})"$'\n'
done

# ---------------------------------------------------------------------------
# Detect runner
# ---------------------------------------------------------------------------
if [[ -z "$RUNNER" ]]; then
  if command -v opencode &>/dev/null; then
    RUNNER="opencode"
  elif command -v claude &>/dev/null || npx @anthropic-ai/claude-code --version &>/dev/null 2>&1; then
    RUNNER="claude"
  else
    echo "Error: no agent runner found. Install opencode or claude-code."
    exit 1
  fi
fi
echo "Runner:  $RUNNER"

# ---------------------------------------------------------------------------
# Build prompts and spawn
# ---------------------------------------------------------------------------
echo ""

build_prompt() {
  local name="$1" role="$2" token="$3"
  local role_instruction="${ROLE_DESCRIPTIONS[$role]:-You are a team member. Wait for instructions before acting.}"
  cat <<PROMPT
You are ${name}, working on a team task via the chait collaboration server.

## Critical rules
- Do not guess. Base every statement on facts — research the code, read the docs, verify. If you have a hypothesis, say so explicitly and suggest how to prove it.
- Be impartial, pragmatic, direct. Never agree with what anyone says just to be polite. Disagree when you see problems.
- Do not act without being asked. If you are PM or Lead, you drive the conversation and assign work. If you are an engineer, wait for the PM or Lead to instruct you before doing anything.
- When reviewing work, be thorough and picky. Group findings by importance. Do not soften feedback.
- Keep messages concise (2-4 sentences). No filler, no preamble.

## Your role
${role_instruction}

## Task
${TASK}

## How to communicate
Read the API instructions: curl -s ${SERVER}/api/v1/instructions

Your auth token: ${token}
Chat room: ${ROOM}

Your team:
${ROSTER}
## Workflow
1. Check existing messages: curl -s "${SERVER}/api/v1/rooms/${ROOM}/messages" -H "Authorization: Bearer ${token}"
2. Enter a loop:
   a. Poll for new messages: curl -s "${SERVER}/api/v1/me/unread?wait=60&since=<newest_created_at_seen>" -H "Authorization: Bearer ${token}"
      There is no server-side read-tracking: omitting \`since\` re-returns everything from the last 10 minutes on every call. After your first call, track the newest \`created_at\` you've received (across room_messages, dms, and documents) and pass it as \`since\` on every subsequent call, or you will keep seeing the same messages.
   b. Read messages. Only respond when you have something substantive to contribute or when someone addresses you.
   c. Repeat until the task is resolved.
3. Use DMs for private side-conversations when appropriate.
4. Upload documents (design docs, code) to share artifacts with the room.
5. When the task is done, update room status: curl -s -X POST "${SERVER}/api/v1/rooms/${ROOM}/status" -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" -d '{"status": "completed"}'

Messages from humans have priority:true — drop everything and address those first.
PROMPT
}

spawn_agent() {
  local role="$1" name="$2" prompt="$3" logfile="/tmp/chait-agent-${role}-${ROOM}.log"

  case "$RUNNER" in
    opencode)
      opencode run --model "google-vertex-anthropic/${MODEL}" "$prompt" \
        > "$logfile" 2>&1 &
      ;;
    claude)
      npx @anthropic-ai/claude-code -p "$prompt" --model "$MODEL" \
        --add-dir "$CONTEXT" \
        > "$logfile" 2>&1 &
      ;;
    *)
      echo "Unknown runner: $RUNNER"; exit 1 ;;
  esac
}

PIDS=()
for i in "${!ROLES[@]}"; do
  role="${ROLES[$i]}"
  name="${NAMES[$i]}"
  token="${TOKENS[$i]}"
  prompt="$(build_prompt "$name" "$role" "$token")"

  if $DRY_RUN; then
    echo "=== PROMPT for ${name} (${role}) ==="
    echo "$prompt"
    echo ""
  else
    echo "Spawning: ${name} (${role})..."
    spawn_agent "$role" "$name" "$prompt"
    PIDS+=($!)
  fi
done

if $DRY_RUN; then
  echo "Dry run complete. No agents spawned."
  exit 0
fi

echo ""
echo "All agents spawned. PIDs: ${PIDS[*]}"
echo "Room: $SERVER (login to web UI to observe)"
echo "Logs: /tmp/chait-agent-*-${ROOM}.log"
echo ""
echo "Press Ctrl+C to stop all agents, or wait for them to finish."

cleanup() {
  echo ""
  echo "Stopping agents..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null
  echo "Done."
}
trap cleanup INT TERM

wait "${PIDS[@]}" 2>/dev/null
echo ""
echo "All agents finished."
echo "Check the conversation at: ${SERVER} (room: ${ROOM})"
