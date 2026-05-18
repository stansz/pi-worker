# pi-worker

Lightweight HTTP endpoint that spawns one-shot [Pi](https://pi.dev) coding agents.

Pi runs, does the work, exits. No persistent agent process. No self-modifying behavior.

## Architecture

```
Hermes → POST /run { prompt, projects } → pi-worker → Pi → stdout
                                              │
                                              ├── scans project docs (AGENTS.md, README.md)
                                              ├── spawns pi -p "context + prompt" --no-session
                                              ├── captures output (90s timeout)
                                              ├── git commit + push on success
                                              └── logs to runs.jsonl
```

## Install

```bash
curl -sL https://raw.githubusercontent.com/stansz/pi-worker/main/install.sh | bash
```

## Files

| File | Purpose |
|------|---------|
| `listener.py` | HTTP server — spawns Pi, commits, logs |
| `projects.yaml` | Project directory — paths, docs, services per VPS |
| `pi-worker.service` | systemd user unit |
| `install.sh` | Bootstrap fresh VPS |
| `update.sh` | Git pull + npm update + restart |
| `runs.jsonl` | Run log (auto-created on first run) |

## API

### POST /run
```json
{
  "prompt": "fix the elevation query to use materialized view",
  "projects": ["geo-api"]
}
```
Header: `X-Api-Key: <shared-secret>`

Response:
```json
{
  "output": "...",
  "exit_code": 0,
  "duration": 12.4,
  "git": {"geo-api": "committed"}
}
```

### GET /health
```json
{"status": "ok", "timestamp": "..."}
```

### GET /history?limit=20
Recent run log entries.

## Security

- Binds to `127.0.0.1` only — no public port
- Shared secret via `X-Api-Key` header
- Expose via Cloudflare Tunnel (Zero Trust dashboard)
- Pi runs as normal user — no sudo
