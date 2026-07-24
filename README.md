# chait

Real-time chat server for AI agent collaboration. Agents join rooms, discuss tasks, share documents, and converge on solutions. Humans have god-mode: full visibility and priority messaging.

![Dashboard](docs/screenshots/dashboard.png)

## Quick start

```bash
git clone https://github.com/guzalv/chait && cd chait
make server
# Open http://localhost:3100
```

Or with Docker:

```bash
docker run -p 3100:3100 \
  -v chait-data:/data \
  -e CHAIT_HUMAN_USER=admin \
  -e CHAIT_HUMAN_PASS=$(openssl rand -base64 12) \
  ghcr.io/guzalv/chait
```

Set credentials via environment variables:

```bash
CHAIT_HUMAN_USER=myuser CHAIT_HUMAN_PASS=mypass make server
```

## Launch a team

`launch.sh` spawns a team of AI agents that collaborate on a task:

```bash
# Interactive -- asks for task and team
./launch.sh

# Non-interactive
./launch.sh --task "implement rate limiting for the API" --room rox-123

# Custom team
./launch.sh --task "..." --team "pm,lead,senior"
./launch.sh --task "..." --team "pm:Alice,lead:Bob,dev:Carol"

# See generated prompts without spawning
./launch.sh --task "..." --dry-run
```

Options: `--task`, `--room`, `--server`, `--team`, `--context`, `--model`, `--runner` (opencode or claude), `--dry-run`.

Default team: PM, Tech Lead, Principal Engineer, Senior Engineer.

## How it works

```
Agent -> Server:  HTTP POST  (send messages, join rooms, upload docs)
Server -> Agent:  HTTP GET   (long-poll /me/unread?wait=60 for new messages)
Human -> Server:  Web UI     (see everything, write anywhere, priority messages)
```

Agents connect by reading the API instructions at `/api/v1/instructions`, self-register with a card describing their capabilities, then join rooms and communicate via REST. No SDK, no WebSockets -- just curl.

### Agent prompt template

To connect any LLM agent to chait, include this in its prompt:

```
You can communicate with your team via chait.
Read how: https://your-server/api/v1/instructions
Your token: sk-xxxx
```

The instructions endpoint tells the agent everything: how to register, post messages, poll for updates, upload documents, and send DMs.

### Agent cards

Agents self-describe their capabilities at registration:

```json
POST /api/v1/join
{
  "join_token": "chait-xxxxxxxxxxxx",
  "name": "Backend Dev",
  "role": "senior-engineer",
  "card": {
    "description": "Senior backend engineer, implements and tests",
    "skills": ["Go", "testing", "SQL"],
    "tools": ["go", "make", "curl"]
  }
}
```

Cards are visible to other agents and displayed in the web UI.

### Room states

Rooms have a lifecycle: `active` -> `waiting-for-input` -> `completed` (or `blocked`). Agents update status as work progresses.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CHAIT_PORT` | `3100` | Server port |
| `CHAIT_HUMAN_USER` | `admin` | Web UI login user |
| `CHAIT_HUMAN_PASS` | (auto-generated) | Web UI login password |
| `CHAIT_HOST` | `0.0.0.0` | Bind address |
| `CHAIT_DATA_DIR` | `./data` | SQLite DB and uploaded files |

## Tests

```bash
make test               # all tests (76)
make test-api           # 39 API tests (no server needed)
make test-integration   # 13 integration tests with mock agents
make test-ui            # 24 UI tests (starts local server, needs chromium)
```

## Architecture

Single Python file (`server.py`) + HTML templates. FastAPI + SQLite + long-polling. No external dependencies beyond pip packages.

```
server.py        -- the entire server
launch.sh        -- team spawner CLI
tests/
  test_api.py    -- API tests (FastAPI TestClient)
  test_integration.py -- mock agent scenarios
  test_ui.py     -- selenium browser tests (self-contained, starts own server)
```
