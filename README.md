# 🔄 Freerouter for Hermes

> **Keep your Hermes Agent on the best free models — automatically.**

Freerouter fetches all free models from OpenRouter, scores them with a weighted algorithm, runs health-checks, and updates your Hermes Agent config. Set it up once and forget it — a daily cron job keeps your models current.

## Why?

OpenRouter adds, removes, and changes free models every day. Manually tracking which models are available, testing which ones work, and updating your config is tedious. Freerouter automates all of it so your Hermes Agent always runs on the best free models available.

## What It Does

1. **Fetches** all current free models from the OpenRouter API
2. **Scores** each model using a weighted multi-dimensional algorithm (context, popularity, features, speed, quality)
3. **Health-checks** the top candidates with real API calls
4. **Updates** your Hermes Agent `config.yaml` with the best models
5. **Notifies** you via Telegram (optional)
6. **Restarts** the Hermes Gateway (only when models actually changed)

## Quick Start

### Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed
- [OpenRouter API Key](https://openrouter.ai/keys) (free)
- Telegram Bot Token (optional, for notifications)

### Install

```bash
# Clone the repo
git clone https://github.com/KrabbiAI/freerouter-for-hermes.git
cd freerouter-for-hermes

# Run the installer
bash install.sh
```

The installer checks prerequisites, copies the script, and runs a dry-run test.

### Set Your API Key

```bash
nano ~/.hermes/.env
# Add: OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

### Test It

```bash
# Dry run (no changes)
DRY_RUN=true python3 ~/.hermes/scripts/freerouter.py

# Live run (updates config)
DRY_RUN=false python3 ~/.hermes/scripts/freerouter.py
```

### Set Up Daily Cron

```bash
hermes cron create '0 6 * * *' \
  --prompt 'Run Freerouter (live mode). Script: ~/.hermes/scripts/freerouter.py. Set DRY_RUN=false.' \
  --name 'Freerouter' \
  --toolsets terminal
```

That's it. Freerouter runs every day at 06:00 and keeps your models current.

## How It Works

### Scoring

Each free model is scored across 5 dimensions:

| Dimension | Weight | Measures |
|---|---|---|
| Context | 20% | Context window size (32k=0, 1M=100) |
| Trending | 25% | Popularity rank |
| Feature | 30% | Multimodal, tool calling, coding, vision |
| Speed | 10% | Free=100, paid=scaled by price |
| Quality | 15% | Composite of context + modality + tools |

Models must pass hard filters: minimum 128k context, must be free, not banned, not reasoning-only.

### Health Checks

The top 3 models per category are tested with real API calls before selection. Models that fail 3 times are banned for 24 hours.

### Idempotent

Freerouter only changes your config when models actually shift. No changes = no config write = no gateway restart.

### Fallback Chains

The top 5 models per category are saved. If the #1 model goes down, the next one is ready.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | — | OpenRouter API key |
| `TELEGRAM_BOT_TOKEN` | No | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | — | Your Telegram chat ID |
| `DRY_RUN` | No | `true` | Set to `false` for live runs |
| `HERMES_HOME` | No | `~/.hermes` | Path to Hermes directory |

## Troubleshooting

| Problem | Solution |
|---|---|
| "OPENROUTER_API_KEY not found" | Add key to `~/.hermes/.env` |
| "API Call failed" / 401 | Generate new key at openrouter.ai/keys |
| "Gateway restart failed" | Run `hermes gateway restart` manually |
| Telegram not working | Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env` |
| Config not updating | Make sure `DRY_RUN=false` |

## For AI Agents

If you're an AI Agent looking to replicate this setup from scratch, see **[AGENT_SETUP.md](AGENT_SETUP.md)** — it contains a step-by-step guide to build the entire system: scoring algorithm, health checks, failure tracking, config patching, cron setup, and Telegram notifications.

## License

MIT
