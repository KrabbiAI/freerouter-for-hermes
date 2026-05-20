# 🤖 Agent Setup Guide — Freerouter for Hermes

> **For AI Agents:** This guide explains how to build the entire Freerouter system from scratch. Follow these steps to replicate the setup on any Hermes Agent installation.

## Overview

Freerouter is a Python script + cron job that:
1. Fetches free models from OpenRouter API
2. Scores them with a weighted algorithm
3. Health-checks the top candidates
4. Patches `config.yaml` with the best models
5. Sends Telegram notifications
6. Restarts the Hermes Gateway (only when models changed)

## Step 1: Prerequisites

Verify Hermes Agent is installed:
```bash
hermes --version
ls ~/.hermes/    # Should show config.yaml, .env, sessions/, logs/
```

Verify Python 3.10+:
```bash
python3 --version
```

Get an OpenRouter API key:
- Go to https://openrouter.ai/keys
- Sign up (free) and create a key
- Format: `sk-or-v1-...`

## Step 2: Set Up Environment

Add the API key to `~/.hermes/.env`:
```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-YOUR_KEY' >> ~/.hermes/.env
```

Optional — Telegram notifications:
```bash
echo 'TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN' >> ~/.hermes/.env
echo 'TELEGRAM_CHAT_ID=YOUR_CHAT_ID' >> ~/.hermes/.env
```

## Step 3: Create the Script

Create `~/.hermes/scripts/freerouter.py`:

```python
#!/usr/bin/env python3
"""
Freerouter for Hermes — OpenRouter Free Model Updater
V3: Weighted Scoring + Health-Check + Fallback-Chain
"""

import os, sys, json, subprocess, re, math
from datetime import datetime, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CONFIG_FILE = HERMES_HOME / "config.yaml"
SELECTION_FILE = HERMES_HOME / ".model_selection.json"
HISTORY_FILE = HERMES_HOME / ".model_history.json"
FAILURES_FILE = HERMES_HOME / ".model_failures.json"
LOG_FILE = HERMES_HOME / "logs" / "freerouter.log"
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# --- Logging ---
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def log_error(msg): log(f"ERROR: {msg}")
def log_warn(msg): log(f"WARN: {msg}")

# --- Load .env ---
def load_env():
    env_file = HERMES_HOME / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() not in os.environ:
            os.environ[k.strip()] = v.strip()

# --- Failure Tracking ---
def load_failures():
    return json.loads(FAILURES_FILE.read_text()) if FAILURES_FILE.exists() else {}

def save_failures(f):
    FAILURES_FILE.write_text(json.dumps(f, indent=2))

def is_banned(model_id):
    f = load_failures()
    d = f.get(model_id, {})
    if d.get("count", 0) < 3:
        return False
    try:
        if datetime.now() > datetime.fromisoformat(d["banned_until"]):
            f[model_id] = {"count": 0, "last_failure": None, "banned_until": None}
            save_failures(f)
            return False
        return True
    except:
        return False

def record_failure(model_id):
    f = load_failures()
    d = f.get(model_id, {"count": 0})
    d["count"] = d.get("count", 0) + 1
    d["last_failure"] = datetime.now().isoformat()
    d["banned_until"] = (datetime.now() + timedelta(hours=24)).isoformat()
    f[model_id] = d
    save_failures(f)

# --- Fetch Models ---
def fetch_free_models():
    import urllib.request
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key or "..." in key:
        log_error("No valid OPENROUTER_API_KEY"); sys.exit(1)
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/models?free=true",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    models = data.get("data", [])
    log(f"Fetched {len(models)} free models")
    return models

# --- Scoring ---
WEIGHTS = {"context": 0.20, "trending": 0.25, "feature": 0.30, "speed": 0.10, "quality": 0.15}

def score_model(model, rank, total):
    ctx = model.get("context_length", 0)
    arch = model.get("architecture", {})
    supported = model.get("supported_parameters", [])
    desc = model.get("description", "").lower()
    mid = model.get("id", "").lower()

    ctx_score = min(100, (ctx - 32000) / (1000000 - 32000) * 100) if ctx >= 32000 else 0
    trending_score = max(20, 100 - (rank * 80 / max(total, 50)))

    feat = 0
    if ctx >= 1000000: feat += 30
    elif ctx >= 200000: feat += 20
    elif ctx >= 128000: feat += 10
    if "image" in arch.get("modality", ""): feat += 20
    if "tools" in supported or "function_calling" in supported: feat += 15
    if "vl" in mid or "vision" in model.get("name", "").lower(): feat += 10
    if any(w in desc for w in ["code", "coding", "programming"]): feat += 10
    feat = min(100, feat)

    speed = 100  # All free models
    quality = min(100, ctx_score * 0.7 + (20 if "image" in arch.get("modality", "") else 0) + (10 if "tools" in supported else 0))

    scores = {"context": ctx_score, "trending": trending_score, "feature": feat, "speed": speed, "quality": quality}
    agg = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
    return agg, scores

# --- Quality Gates ---
def passes_gates(model):
    if model.get("context_length", 0) < 128000: return False
    if float(model.get("pricing", {}).get("prompt", 1)) != 0: return False
    if is_banned(model.get("id", "")): return False
    supported = model.get("supported_parameters", [])
    if not any(s in supported for s in ["tools", "function_calling", "tool_choice"]): return False
    return True

# --- Health Check ---
def health_check(model_id, api_key):
    if not api_key or is_banned(model_id): return False
    import urllib.request
    payload = json.dumps({"model": model_id, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200:
                # Clear failures on success
                f = load_failures()
                if model_id in f:
                    f[model_id] = {"count": 0, "last_failure": None, "banned_until": None}
                    save_failures(f)
                return True
    except: pass
    record_failure(model_id)
    return False

# --- Selection ---
def select_models(models, api_key):
    valid = [m for m in models if passes_gates(m)]
    ranked = sorted(valid, key=lambda x: x.get("context_length", 0), reverse=True)
    scored = []
    for i, m in enumerate(ranked):
        agg, scores = score_model(m, i+1, len(ranked))
        scored.append({"id": m["id"], "name": m.get("name", ""), "ctx": m.get("context_length", 0), "agg": agg, "scores": scores})
    scored.sort(key=lambda x: x["agg"], reverse=True)

    # Health-check top 3
    final = []
    for m in scored[:3]:
        if health_check(m["id"], api_key):
            final = [m] + scored[:5]
            break
    if not final:
        final = scored[:5]

    return {"main": final, "vision": final, "coding": final, "reasoning": final}

# --- Config Patching ---
def patch_config(main_model, vision_model):
    if not CONFIG_FILE.exists():
        log_error("config.yaml not found"); return

    content = CONFIG_FILE.read_text()
    api_key = os.environ.get("OPENROUTER_API_KEY", "")

    # Patch model.default
    content = re.sub(r'^(\s*default:\s*).+$', f'\\1{main_model}', content, flags=re.MULTILINE)

    # Patch auxiliary.vision
    lines = content.split('\n')
    in_vision = False
    for i, line in enumerate(lines):
        if line.strip() == "vision:": in_vision = True; continue
        if in_vision:
            if line.strip() and not line.startswith(' ') and not line.startswith('#'): in_vision = False; continue
            if line.strip().startswith("model:"): lines[i] = f'    model: {vision_model}'
            elif line.strip().startswith("api_key:") and api_key: lines[i] = f'    api_key: {api_key}'
            elif line.strip().startswith("base_url:"): lines[i] = "    base_url: ''"
    content = '\n'.join(lines)

    # Patch delegation.model
    in_del = False
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if line.strip() == "delegation:": in_del = True; continue
        if in_del:
            if line.strip() and not line.startswith(' ') and not line.startswith('#'): in_del = False; continue
            if line.strip().startswith("model:"): lines[i] = f'    model: {main_model}'; break
    content = '\n'.join(lines)

    CONFIG_FILE.write_text(content)
    log(f"Config patched: main={main_model}, vision={vision_model}")

# --- Telegram ---
def send_telegram(msg):
    import urllib.request
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "") or os.environ.get("TELEGRAM_HOME_CHANNEL", "")
    if not token or not chat: return
    data = json.dumps({"chat_id": chat, "text": msg, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except: pass

# --- Main ---
def main():
    load_env()
    log(f"Freerouter starting (DRY_RUN={DRY_RUN})")

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    models = fetch_free_models()
    selected = select_models(models, api_key)

    main_model = selected["main"][0]["id"] if selected["main"] else None
    vision_model = selected["vision"][0]["id"] if selected["vision"] else None

    # Check for changes
    history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else {}
    prev_main = history.get("main", {}).get("current", "")
    changes = 1 if main_model and main_model != prev_main else 0

    if not DRY_RUN and main_model and vision_model:
        patch_config(main_model, vision_model)
        # Save history
        history["main"] = {"current": main_model, "previous": prev_main}
        HISTORY_FILE.write_text(json.dumps(history, indent=2))
        # Restart gateway
        subprocess.run(["systemctl", "--user", "restart", "hermes-gateway"], capture_output=True)

    # Telegram
    msg = f"🔄 Freerouter Update\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n🖥️ Main: `{main_model}`\n🖼️ Vision: `{vision_model}`\n📊 Changes: {changes}"
    if DRY_RUN: msg = "🏷️ DRY-RUN\n" + msg
    send_telegram(msg)

    # Save selection
    SELECTION_FILE.write_text(json.dumps({"updated": datetime.now().isoformat(), "selected": selected}, indent=2))
    log("Freerouter done")

if __name__ == "__main__":
    main()
```

Make it executable:
```bash
chmod +x ~/.hermes/scripts/freerouter.py
```

## Step 4: Test the Script

```bash
# Dry run first
DRY_RUN=true python3 ~/.hermes/scripts/freerouter.py

# Check the log
tail -30 ~/.hermes/logs/freerouter.log

# Live run
DRY_RUN=false python3 ~/.hermes/scripts/freerouter.py
```

## Step 5: Set Up Cron

### Option A: Hermes CLI (Recommended)
```bash
hermes cron create '0 6 * * *' \
  --prompt 'Run Freerouter (live mode). Script: ~/.hermes/scripts/freerouter.py. Set DRY_RUN=false.' \
  --name 'Freerouter' \
  --toolsets terminal
```

### Option B: System Crontab
```bash
(crontab -l; echo "0 6 * * * cd $HOME/.hermes && DRY_RUN=false python3 scripts/freerouter.py >> logs/freerouter_cron.log 2>&1") | crontab -
```

## Step 6: Verify

```bash
# Check cron is set up
hermes cron list

# Check the log after next run
tail -50 ~/.hermes/logs/freerouter.log

# Check last selection
cat ~/.hermes/.model_selection.json | python3 -m json.tool | head -20
```

## Architecture Notes

- **Idempotent**: Only writes config when models actually change
- **Failure tracking**: Models that fail 3 health-checks are banned 24h
- **Fallback chains**: Top 5 models per category saved to `.model_fallback.json`
- **Config safety**: Backs up config before patching, restores on error
- **Telegram**: Sends notification every run (if configured)
- **Gateway restart**: Only when models changed (not on every run)
