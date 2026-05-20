# 🔄 OpenRouter Free Model Updater

> **Automated model selection for [Hermes Agent](https://github.com/NousResearch/hermes-agent)**
> Fetches all free models from OpenRouter, scores them with a weighted algorithm, runs health-checks, and updates your config — fully automated via cron.

## Table of Contents

- [What It Does](#what-it-does)
- [Prerequisites](#prerequisites)
- [Quick Start (5 Minutes)](#quick-start-5-minutes)
- [How It Works](#how-it-works)
- [Scoring Algorithm](#scoring-algorithm)
- [Configuration](#configuration)
- [Cron Setup](#cron-setup)
- [Troubleshooting](#troubleshooting)
- [File Structure](#file-structure)
- [License](#license)

---

## What It Does

Every day, OpenRouter adds, removes, or changes free models. This tool:

1. **Fetches** all current free models from the OpenRouter API
2. **Scores** each model using a weighted multi-dimensional algorithm
3. **Health-checks** the top candidates with real API calls
4. **Updates** your Hermes Agent `config.yaml` with the best models
5. **Notifies** you via Telegram (optional)
6. **Restarts** the Hermes Gateway (only when models actually changed)

The result: your Hermes Agent always uses the best available free models — without manual intervention.

---

## Prerequisites

Before installing, make sure you have:

| Requirement | How to Get It | Required? |
|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh \| bash` | ✅ Yes |
| [OpenRouter API Key](https://openrouter.ai/keys) | Sign up free at openrouter.ai → Keys | ✅ Yes |
| [Telegram Bot Token](https://t.me/BotFather) | Message @BotFather → `/newbot` | ❌ Optional |
| Python 3.10+ | Usually pre-installed on Linux | ✅ Yes |
| `gh` CLI (for repo setup) | `gh auth login` | ❌ Optional |

### Verify Hermes Agent is installed

```bash
hermes --version
# Should print something like: hermes-agent 2.x.x

# Check that ~/.hermes exists
ls ~/.hermes/
# Should show: config.yaml, .env, sessions/, logs/, etc.
```

### Verify your OpenRouter API Key

```bash
# Test the key directly:
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer sk-or-v1-YOUR_KEY" \
  "https://openrouter.ai/api/v1/models?free=true&limit=1"
# Should return: 200
```

---

## Quick Start (5 Minutes)

### Step 1: Clone the Repository

```bash
cd ~/workspace
git clone https://github.com/KrabbiAI/openrouter-model-updater.git
cd openrouter-model-updater
```

### Step 2: Run the Installer

```bash
bash install.sh
```

The installer will:
1. Verify Hermes Agent is installed
2. Check that your `.env` file exists
3. Verify your OpenRouter API key is set
4. Copy the updater script to `~/.hermes/scripts/`
5. Run a test (dry-run) to confirm everything works

### Step 3: Set Your API Key (if not already set)

```bash
nano ~/.hermes/.env
```

Find this line and replace `YOUR_KEY_HERE` with your actual key:

```bash
OPENROUTER_API_KEY=sk-or-v1-YOUR_ACTUAL_KEY_HERE
```

Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

### Step 4: Test It

```bash
# Dry run (no changes to config)
DRY_RUN=true python3 ~/.hermes/scripts/openrouter_model_updater.py

# Live run (actually updates config)
DRY_RUN=false python3 ~/.hermes/scripts/openrouter_model_updater.py
```

### Step 5: Set Up the Daily Cron Job

```bash
hermes cron create '0 6 * * *' \
  --prompt 'Run the OpenRouter Model Updater (live mode, not dry-run). Script: ~/.hermes/scripts/openrouter_model_updater.py. Environment: DRY_RUN=false.' \
  --name 'OpenRouter Model Updater' \
  --toolsets terminal
```

That's it. The updater runs every day at 06:00 and keeps your models current.

---

## How It Works

### Pipeline Overview

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  1. FETCH   │───▶│  2. SCORE   │───▶│ 3. HEALTH   │───▶│ 4. UPDATE   │
│             │    │             │    │   CHECK     │    │   CONFIG    │
│ Get all     │    │ Calculate   │    │ Test top 3  │    │ Write best  │
│ free models │    │ weighted    │    │ models via  │    │ models to   │
│ from        │    │ scores for  │    │ real API    │    │ config.yaml │
│ OpenRouter  │    │ each model  │    │ calls       │    │             │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                                                                │
                                                                ▼
                                                       ┌─────────────┐
                                                       │ 5. NOTIFY   │
                                                       │             │
                                                       │ Send        │
                                                       │ Telegram    │
                                                       │ message     │
                                                       └─────────────┘
                                                                │
                                                                ▼
                                                       ┌─────────────┐
                                                       │ 6. RESTART  │
                                                       │             │
                                                       │ Restart     │
                                                       │ Hermes      │
                                                       │ Gateway     │
                                                       │ (only if    │
                                                       │ changed)    │
                                                       └─────────────┘
```

### Idempotent by Design

The updater **only changes your config when models actually shift**. If the top models are the same as yesterday, nothing is written and no restart happens. This means:

- Safe to run multiple times per day
- No unnecessary gateway restarts
- No config file churn

### Failure Tracking

Models that fail health-checks are tracked in `~/.hermes/.model_failures.json`:

- **1 failure**: Logged, model retried next run
- **3 failures**: Model is **banned for 24 hours**
- **After 24h**: Ban expires, failure count resets

This prevents the updater from repeatedly trying broken models.

### Fallback Chains

For each category (main, vision, coding, reasoning), the top 5 models are saved to `~/.hermes/.model_fallback.json`. If the #1 model goes down, the next one is ready.

---

## Scoring Algorithm

Each free model gets a **weighted score** across 5 dimensions. The weights sum to 1.0.

### Dimensions

| Dimension | Weight | What It Measures | Formula |
|---|---|---|---|
| **Context** | 20% | Context window size | `normalize(ctx, 32k, 1M)` → 32k=0, 1M=100 |
| **Trending** | 25% | Popularity rank | Position-based: rank #1=100, rank #50+=20 |
| **Feature** | 30% | Capability bonuses | See feature scoring table below |
| **Speed** | 10% | Cost efficiency | Free=100, paid=scaled by price |
| **Quality** | 15% | Composite quality | `ctx*0.7 + multimodal_bonus + tools_bonus` |

### Feature Score Breakdown

| Feature | Points | Condition |
|---|---|---|
| Large context | +30 | Context ≥ 1M |
| Medium context | +20 | Context ≥ 200k |
| Small context | +10 | Context ≥ 128k |
| Multimodal | +20 | Supports image input |
| Tool calling | +15 | Supports `tools` or `function_calling` |
| Vision-specific | +10 | Model ID/name contains `vl` or `vision` |
| Coding-focused | +10 | Description mentions code/programming |
| Fast/efficient | +5 | Description mentions speed/efficiency |
| Reasoning support | +10 | Supports `include_reasoning` or `reasoning` |

### Quality Gates (Hard Filters)

Models must pass ALL of these to be considered:

- **Minimum context**: 128,000 tokens
- **Must be free**: Prompt price = $0
- **Not banned**: Fewer than 3 failures in the last 24 hours
- **Not reasoning-only**: Must support normal chat/tools (not just chain-of-thought)

### Aggregate Score

```
score = (context × 0.20) + (trending × 0.25) + (feature × 0.30) + (speed × 0.10) + (quality × 0.15)
```

Models are ranked by aggregate score within each category. The top 5 per category become the fallback chain.

---

## Configuration

### Environment Variables

Set these in `~/.hermes/.env`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | ✅ Yes | — | Your OpenRouter API key |
| `TELEGRAM_BOT_TOKEN` | No | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | — | Your Telegram chat ID |
| `TELEGRAM_HOME_CHANNEL` | No | — | Alternative to CHAT_ID |
| `DRY_RUN` | No | `true` | Set to `false` for live runs |
| `HERMES_HOME` | No | `~/.hermes` | Path to Hermes directory |

### Config File Changes

The updater modifies these sections in `~/.hermes/config.yaml`:

```yaml
model:
  default: openrouter/owl-alpha          # ← Updated to best main model
  provider: openrouter
  base_url: ''                           # ← Always '' for OpenRouter

auxiliary:
  vision:
    provider: openrouter
    model: openrouter/owl-alpha          # ← Updated to best vision model
    base_url: ''                         # ← Always '' for OpenRouter
    api_key: sk-or-v1-...                # ← Set from .env

delegation:
  model: openrouter/owl-alpha            # ← Updated to best main model
```

**Important**: The updater sets `base_url: ''` for all OpenRouter providers. This is required — OpenRouter handles routing internally.

### What Gets Patched

The script uses regex-based patching to update config.yaml. It:

1. Backs up the current config to `config.yaml.bak`
2. Updates `model.default` to the best main model
3. Updates `auxiliary.vision.model` to the best vision model
4. Updates `auxiliary.vision.api_key` from the environment
5. Updates `auxiliary.vision.base_url` to `''` if needed
6. Updates `delegation.model` to the best main model
7. On error: restores the backup automatically

---

## Cron Setup

### Option A: Hermes CLI (Recommended)

This creates a managed cron job inside Hermes:

```bash
hermes cron create '0 6 * * *' \
  --prompt 'Run the OpenRouter Model Updater (live mode). Script: ~/.hermes/scripts/openrouter_model_updater.py. Set DRY_RUN=false before running.' \
  --name 'OpenRouter Model Updater' \
  --toolsets terminal \
  --deliver telegram
```

**Advantages:**
- Managed by Hermes (pause, resume, edit via CLI)
- Delivery to Telegram built-in
- Visible in `hermes cron list`

### Option B: System Crontab

Add to your system crontab for a traditional approach:

```bash
crontab -e
```

Add this line:

```cron
0 6 * * * cd /home/YOUR_USER/.hermes && DRY_RUN=false HERMES_HOME=/home/YOUR_USER/.hermes python3 scripts/openrouter_model_updater.py >> logs/model_updater_cron.log 2>&1
```

Replace `/home/YOUR_USER` with your actual home path.

### Schedule Reference

| Schedule | Cron Expression | Description |
|---|---|---|
| Every 6 hours | `0 */6 * * *` | Aggressive — catches fast changes |
| Daily at 6am | `0 6 * * *` | Recommended — balances freshness and API usage |
| Weekly | `0 6 * * 1` | Conservative — minimal API usage |
| Every 2 hours | `0 */2 * * *` | Very aggressive — high API usage |

### Verify Cron is Working

```bash
# List Hermes cron jobs
hermes cron list

# Check the updater log
tail -50 ~/.hermes/logs/model_updater.log

# Check the last selection
cat ~/.hermes/.model_selection.json | python3 -m json.tool | head -30
```

---

## Troubleshooting

### "OPENROUTER_API_KEY not found"

**Cause**: The key is not in `~/.hermes/.env` or is malformed.

**Fix**:
```bash
nano ~/.hermes/.env
# Add or fix this line:
OPENROUTER_API_KEY=sk-or-v1-your-actual-key-here
```

**Verify**:
```bash
grep OPENROUTER_API_KEY ~/.hermes/.env
# Should show: OPENROUTER_API_KEY=sk-or-v1-...
```

### "API Call failed" / HTTP 401

**Cause**: Invalid or expired API key.

**Fix**:
1. Go to https://openrouter.ai/keys
2. Generate a new key
3. Update `~/.hermes/.env` with the new key

### "Gateway restart failed"

**Cause**: The Hermes Gateway is not running as a systemd service.

**Fix**:
```bash
# Check if gateway is running
hermes gateway status

# If not running, start it
hermes gateway start

# If systemd is not available, the script falls back to pkill -HUP
# You may need to restart manually:
hermes gateway restart
```

### "No healthy model in top-3"

**Cause**: The top 3 scored models all failed their health-checks.

**What happens**: The script uses the #1 model anyway (with a warning). The failed models get failure counts incremented.

**Fix**: Usually resolves itself — banned models expire after 24h. If persistent, check your OpenRouter account status.

### Telegram messages not received

**Cause**: Missing or incorrect Telegram credentials.

**Fix**:
```bash
nano ~/.hermes/.env
# Add:
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
```

**Get your chat ID**: Message @userinfobot on Telegram.

### Config not being updated

**Cause**: `DRY_RUN` is set to `true` (the default).

**Fix**:
```bash
# For one-time live run:
DRY_RUN=false python3 ~/.hermes/scripts/openrouter_model_updater.py

# For cron: make sure the prompt sets DRY_RUN=false
```

### "Model changed but config looks the same"

**Cause**: The updater only patches specific lines. If your config has a non-standard format, the regex might not match.

**Fix**: Check the log for patching details:
```bash
grep "Patched\|patching\|base_url" ~/.hermes/logs/model_updater.log | tail -20
```

---

## File Structure

```
openrouter-model-updater/
├── scripts/
│   └── openrouter_model_updater.py       # Main updater script (V3)
├── templates/
│   ├── .env.template                     # Template for ~/.hermes/.env
│   └── config.yaml.template              # Minimal config.yaml template
├── install.sh                            # One-command installer
├── .gitignore                            # Prevents committing secrets
└── README.md                             # This file

# Files created during operation (in ~/.hermes/):
#
# .model_selection.json    — Last selection results (all categories)
# .model_history.json      — History of model changes over time
# .model_failures.json     — Failure tracking and ban status
# .model_fallback.json     — Top-5 fallback chains per category
# config.yaml.bak          — Auto-backup before each config change
# logs/
#   model_updater.log      — Full operation log
```

---

## License

MIT — Use freely, modify as you wish. No warranty.

---

## Contributing

This tool is designed for Hermes Agent. If you adapt it for other agents:

- The scoring algorithm is model-agnostic
- The config patching is Hermes-specific (regex-based YAML editing)
- The OpenRouter API calls are standard REST

Pull requests welcome at https://github.com/KrabbiAI/openrouter-model-updater
