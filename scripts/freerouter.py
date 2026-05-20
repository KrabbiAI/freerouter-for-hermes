#!/usr/bin/env python3
"""
Freerouter for Hermes — OpenRouter Free Model Updater
=====================================================
V3 — Weighted Scoring + Health-Check + Fallback-Chain

Requirements:
  - Python 3.10+
  - Hermes Agent (https://github.com/NousResearch/hermes-agent)
  - OpenRouter API Key (https://openrouter.ai/keys)
  - Telegram Bot Token (optional, for notifications)

Usage:
  DRY_RUN=true  python3 freerouter.py   # Test run
  DRY_RUN=false python3 freerouter.py   # Live run (updates config)
"""

import os
import sys
import json
import subprocess
import re
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

# ========================
# CONFIG
# ========================
HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
CONFIG_FILE = HERMES_HOME / "config.yaml"
CONFIG_BACKUP = HERMES_HOME / "config.yaml.bak"
SELECTION_FILE = HERMES_HOME / ".model_selection.json"
HISTORY_FILE = HERMES_HOME / ".model_history.json"
FAILURES_FILE = HERMES_HOME / ".model_failures.json"
LOG_FILE = HERMES_HOME / "logs" / "freerouter.log"

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

# ========================
# LOGGING
# ========================
def log(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def log_error(msg: str):
    log(f"ERROR: {msg}")

def log_warn(msg: str):
    log(f"WARN: {msg}")

# ========================
# FAILURE TRACKING
# ========================
def load_failures() -> dict:
    if FAILURES_FILE.exists():
        with open(FAILURES_FILE) as f:
            return json.load(f)
    return {}

def save_failures(failures: dict):
    with open(FAILURES_FILE, "w") as f:
        json.dump(failures, f, indent=2)

def record_failure(model_id: str):
    failures = load_failures()
    if model_id not in failures:
        failures[model_id] = {"count": 0, "last_failure": None, "banned_until": None}
    failures[model_id]["count"] += 1
    failures[model_id]["last_failure"] = datetime.now().isoformat()
    ban_until = datetime.now() + timedelta(hours=24)
    failures[model_id]["banned_until"] = ban_until.isoformat()
    save_failures(failures)
    log_warn(f"Model failed {failures[model_id]['count']}x: {model_id} (banned until {failures[model_id]['banned_until']})")

def is_model_banned(model_id: str, max_failures: int = 3) -> bool:
    """Prüft ob Model zu oft fehlgeschlagen hat UND noch within 24h ban period."""
    failures = load_failures()
    model_data = failures.get(model_id, {})
    if model_data.get("count", 0) < max_failures:
        return False
    banned_until_str = model_data.get("banned_until")
    if not banned_until_str:
        return False
    try:
        banned_until = datetime.fromisoformat(banned_until_str)
        if datetime.now() > banned_until:
            failures[model_id] = {"count": 0, "last_failure": None, "banned_until": None}
            save_failures(failures)
            log(f"Model ban expired: {model_id}")
            return False
        return True
    except (ValueError, TypeError):
        return False

def clear_failures(model_id: str):
    failures = load_failures()
    if model_id in failures:
        failures[model_id] = {"count": 0, "last_failure": None, "banned_until": None}
        save_failures(failures)

# ========================
# ENV LOADER
# ========================
def load_env():
    """Lädt .env Datei in Umgebung wenn nicht schon vorhanden."""
    env_file = HERMES_HOME / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if key not in os.environ:
                    os.environ[key] = val

# ========================
# API CALL
# ========================
def fetch_free_models():
    """Lädt alle Free Models von OpenRouter API."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key or "..." in api_key:
        log_error("OPENROUTER_API_KEY nicht in Umgebung gefunden oder redacted")
        sys.exit(1)

    log("Rufe OpenRouter API ab...")

    import urllib.request
    url = "https://openrouter.ai/api/v1/models?free=true"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log_error(f"API Call fehlgeschlagen: {e}")
        sys.exit(1)

    models = data.get("data", [])
    log(f"  {len(models)} Free Models erhalten")
    return models

# ========================
# QUALITY GATES
# ========================
def passes_quality_gates(model: dict) -> bool:
    ctx = model.get("context_length", 0)
    pricing = model.get("pricing", {})

    if ctx < 128_000:
        return False

    prompt_price = float(pricing.get("prompt", 1))
    if prompt_price != 0:
        return False

    model_id = model.get("id", "")
    if is_model_banned(model_id):
        log(f"  Skip (banned): {model_id}")
        return False

    return True

def is_reasoning_only(model: dict) -> bool:
    supported = model.get("supported_parameters", [])
    has_tools = "tools" in supported or "function_calling" in supported or "tool_choice" in supported
    return not has_tools

# ========================
# SCORING V3 — GEWICHTET
# ========================
WEIGHTS = {
    'context': 0.20,
    'trending': 0.25,
    'feature': 0.30,
    'speed': 0.10,
    'quality': 0.15,
}

def normalize(value: float, min_val: float, max_val: float) -> float:
    if max_val == min_val:
        return 50.0
    return 100 * (value - min_val) / (max_val - min_val)

def calculate_context_score(ctx: int) -> float:
    return normalize(ctx, 32_000, 1_000_000)

def calculate_trending_score(position: int, total: int) -> float:
    if total <= 0:
        return 50.0
    return max(20, 100 - (position * 80 / max(total, 50)))

def calculate_feature_scores(model: dict) -> dict:
    name = model.get("name", "").lower()
    model_id = model.get("id", "").lower()
    desc = model.get("description", "").lower()
    arch = model.get("architecture", {})
    modality = arch.get("modality", "")
    supported = model.get("supported_parameters", [])
    ctx = model.get("context_length", 0)

    score = 0

    if ctx >= 1_000_000:
        score += 30
    elif ctx >= 200_000:
        score += 20
    elif ctx >= 128_000:
        score += 10

    if "image" in modality or "text+image" in modality:
        score += 20

    if "tools" in supported or "function_calling" in supported:
        score += 15

    if "vl" in model_id or "vision" in name:
        score += 10

    if any(w in desc for w in ["code", "coding", "programming", "software", "developer"]):
        score += 10

    if any(w in desc for w in ["fast", "efficient", "high-performance"]):
        score += 5

    if "include_reasoning" in supported or "reasoning" in supported:
        score += 10

    return min(100, score)

def calculate_rating_score(rating: float) -> float:
    return (rating / 5) * 100

def weighted_score(scores: dict) -> float:
    return sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)

def calculate_all_scores(model: dict, trending_position: int = 0, total_models: int = 0) -> dict:
    ctx = model.get("context_length", 0)
    rating = model.get("rating", 0)

    context_score = calculate_context_score(ctx)
    trending_score = calculate_trending_score(trending_position, total_models)
    feature_score = calculate_feature_scores(model)

    pricing = model.get("pricing", {})
    prompt_price = float(pricing.get("prompt", 0))
    speed_score = 100 if prompt_price == 0 else max(0, 100 - (prompt_price * 1_000_000))

    quality_score = min(100, context_score * 0.7 + (20 if "image" in model.get("architecture", {}).get("modality", "") else 0) + (10 if "tools" in model.get("supported_parameters", []) else 0))

    rating_bonus = calculate_rating_score(rating) * 0.1

    return {
        "context": context_score,
        "trending": trending_score,
        "feature": feature_score,
        "speed": speed_score,
        "quality": quality_score,
        "rating": rating_bonus
    }

def aggregate_score(scores: dict) -> float:
    return weighted_score(scores)

# ========================
# HEALTH CHECK
# ========================
def get_api_key() -> str:
    """Liest API Key aus Environment (geladen aus .env via load_env())."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key and key != "***" and "..." not in key:
        return key
    return ""

def health_check(model_id: str, api_key: str) -> bool:
    """Testet ob Model tatsächlich antworten kann (kurzer Test-Call)."""
    log(f"  Health-Check: {model_id}")

    if not api_key or api_key == "***":
        log(f"  → Skipping (no API key available)")
        return None

    if is_model_banned(model_id):
        log(f"  → Skipping (banned): {model_id}")
        return False

    import urllib.request

    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5
    }
    data = json.dumps(payload).encode()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                clear_failures(model_id)
                log(f"  ✓ Healthy: {model_id}")
                return True
            else:
                log_warn(f"  ✗ HTTP {resp.status}: {model_id}")
                record_failure(model_id)
                return False
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        err_msg = err_body[:150] if err_body else str(e)
        if e.code == 401:
            log_warn(f"  ⚠ Auth Error für {model_id} — API Key prüfen")
            return False
        log_warn(f"  ✗ HTTP {e.code}: {model_id} — {err_msg}")
        record_failure(model_id)
        return False
    except Exception as e:
        log_warn(f"  ✗ Error: {model_id} — {e}")
        record_failure(model_id)
        return False

# ========================
# SELECTION
# ========================
def select_top_models(models: list, api_key: str) -> dict:
    categorized = {"main": [], "vision": [], "coding": [], "reasoning": []}

    sorted_models = sorted(models, key=lambda x: x.get("context_length", 0), reverse=True)

    position_map = {}
    for i, m in enumerate(sorted_models):
        position_map[m["id"]] = i + 1

    total = len(models)

    for model in models:
        if not passes_quality_gates(model):
            continue

        if is_reasoning_only(model):
            continue

        pos = position_map.get(model["id"], 50)
        scores = calculate_all_scores(model, trending_position=pos, total_models=total)
        agg = aggregate_score(scores)

        for cat in ["main", "vision", "coding", "reasoning"]:
            categorized[cat].append({
                "id": model["id"],
                "name": model.get("name", ""),
                "context_length": model.get("context_length", 0),
                "aggregate": agg,
                "dim_scores": scores
            })

    result = {}
    for cat in categorized:
        sorted_cat = sorted(categorized[cat], key=lambda x: x["aggregate"], reverse=True)
        result[cat] = sorted_cat[:5]

    return result

def select_with_health_check(selected: dict, api_key: str) -> dict:
    final = {}

    if not api_key or api_key == "***":
        log("\n  → Kein API Key — Health-Checks übersprungen")
        return selected

    for cat in ["main", "vision", "coding", "reasoning"]:
        if not selected.get(cat):
            final[cat] = None
            continue

        log(f"\n  === Health-Check für {cat.upper()} ===")

        for m in selected[cat][:3]:
            model_id = m["id"]
            result = health_check(model_id, api_key)

            if result is True:
                m["healthy"] = True
                final[cat] = [m] + selected[cat][:5]
                log(f"  → Selected (healthy): {model_id}")
                break
            elif result is None:
                final[cat] = [m] + selected[cat][:5]
                log(f"  → Selected (unknown): {model_id}")
                break
            else:
                continue
        else:
            log_warn(f"  → No healthy model in top-3, using #1 anyway")
            final[cat] = selected[cat]

    return final

# ========================
# SAFETY UTILITIES
# ========================
def get_current_models() -> dict:
    current = {"main": None, "vision": None, "delegation": None}
    if not CONFIG_FILE.exists():
        return current

    with open(CONFIG_FILE, "r") as f:
        content = f.read()

    m = re.search(r'^(\s*default:\s*).+$', content, re.MULTILINE)
    if m:
        current["main"] = m.group(1).strip()

    m = re.search(r'vision:.*?model:\s*([^\n]+)', content, re.DOTALL)
    if m:
        current["vision"] = m.group(1).strip()

    m = re.search(r'delegation:.*?model:\s*([^\n]+)', content, re.DOTALL)
    if m:
        current["delegation"] = m.group(1).strip()

    return current

def restore_config(current: dict):
    if not CONFIG_FILE.exists():
        return

    with open(CONFIG_FILE, "r") as f:
        content = f.read()

    if current.get("main"):
        if "default:" in content:
            content = re.sub(r'^(\s*default:\s*).+$', f'\\1{current["main"]}', content, flags=re.MULTILINE)
    if current.get("vision"):
        if "vision:" in content:
            content = re.sub(r'vision:.*?model:\s*([^\n]+)', f'model: {current["vision"]}', content, flags=re.DOTALL)
    if current.get("delegation"):
        if "delegation:" in content:
            content = re.sub(r'delegation:.*?model:\s*([^\n]+)', f'model: {current["delegation"]}', content, flags=re.DOTALL)

    with open(CONFIG_FILE, "w") as f:
        f.write(content)

    log("Config wiederhergestellt auf vorherige Werte")

# ========================
# CONFIG UPDATE
# ========================
def backup_config():
    if CONFIG_FILE.exists():
        import shutil
        shutil.copy(CONFIG_FILE, CONFIG_BACKUP)
        log(f"Backup erstellt: {CONFIG_BACKUP}")

def patch_config(main_model: str, vision_model: str, delegation_model: str):
    if not CONFIG_FILE.exists():
        log_error(f"Config nicht gefunden: {CONFIG_FILE}")
        return

    with open(CONFIG_FILE, "r") as f:
        content = f.read()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key or api_key in ("***", "") or "..." in api_key:
        log_warn("Kein gültiger OpenRouter API Key in .env gefunden!")
        api_key = ""

    # Patch model.default
    model_default_pattern = r'^(\s*default:\s*).+$'
    if re.search(model_default_pattern, content, re.MULTILINE):
        content = re.sub(model_default_pattern, f'\\1{main_model}', content, re.MULTILINE)
    else:
        content = re.sub(r'^model:', f'model:\\n  default: {main_model}', content, re.MULTILINE)

    # CRITICAL FIX 1: Set model.base_url to '' when provider=openrouter
    lines = content.split('\n')
    in_model_section = False
    section_indent = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "model:" and (i == 0 or not lines[i-1].startswith(' ')):
            in_model_section = True
            section_indent = len(line) - len(line.lstrip())
            continue

        if in_model_section:
            current_indent = len(line) - len(line.lstrip())
            if stripped and current_indent <= section_indent and not stripped.startswith('#'):
                in_model_section = False
                continue

            if stripped.startswith('base_url:'):
                indent = len(line) - len(line.lstrip())
                provider_line = ''
                for j in range(max(0, i-5), i):
                    if lines[j].strip().startswith('provider:'):
                        provider_line = lines[j].strip()
                        break

                if 'openrouter' in provider_line:
                    lines[i] = ' ' * indent + "base_url: ''  # Fixed by openrouter_model_updater"
                    log("  → model.base_url auf '' gesetzt (provider=openrouter)")

    content = '\n'.join(lines)

    # CRITICAL FIX 2: Set auxiliary.vision.api_key and base_url
    lines = content.split('\n')
    in_vision_section = False
    section_indent = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "vision:":
            in_vision_section = True
            section_indent = len(line) - len(line.lstrip())
            continue

        if in_vision_section:
            current_indent = len(line) - len(line.lstrip())
            if stripped and current_indent <= section_indent:
                in_vision_section = False
                continue

            if stripped.startswith("model:"):
                indent = len(line) - len(line.lstrip())
                lines[i] = ' ' * indent + f'model: {vision_model}'

            elif stripped.startswith("api_key:"):
                indent = len(line) - len(line.lstrip())
                if api_key:
                    lines[i] = ' ' * indent + f'api_key: {api_key}'
                    log(f"  → auxiliary.vision.api_key gesetzt (length: {len(api_key)})")
                else:
                    log_warn("  → Kein API Key für auxiliary.vision.api_key gefunden!")

            elif stripped.startswith("base_url:"):
                indent = len(line) - len(line.lstrip())
                provider_line = ''
                for j in range(max(0, i-10), i):
                    if 'provider:' in lines[j] and 'openrouter' in lines[j]:
                        provider_line = lines[j]
                        break

                if 'openrouter' in provider_line and stripped != "base_url: ''":
                    lines[i] = ' ' * indent + "base_url: ''  # Fixed by openrouter_model_updater"
                    log("  → auxiliary.vision.base_url auf '' gesetzt")

    content = '\n'.join(lines)

    # Patch delegation.model
    lines = content.split('\n')
    in_delegation_section = False
    section_indent = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == "delegation:":
            in_delegation_section = True
            section_indent = len(line) - len(line.lstrip())
            continue

        if in_delegation_section:
            current_indent = len(line) - len(line.lstrip())
            if stripped and current_indent <= section_indent:
                in_delegation_section = False
                continue

            if stripped.startswith("model:"):
                indent = len(line) - len(line.lstrip())
                lines[i] = ' ' * indent + f'model: {delegation_model}'
                break

    content = '\n'.join(lines)

    with open(CONFIG_FILE, "w") as f:
        f.write(content)

    log(f"Config gepatcht: default={main_model}, vision={vision_model}, delegation={delegation_model}")

def restart_gateway():
    log("Versuche Gateway-Restart...")

    result = subprocess.run(
        ["systemctl", "--user", "restart", "hermes-gateway"],
        capture_output=True
    )
    if result.returncode == 0:
        log("Gateway via systemd neugestartet")
        return

    result = subprocess.run(
        ["pkill", "-HUP", "hermes-gateway"],
        capture_output=True
    )
    if result.returncode == 0:
        log("Gateway via pkill neugestartet")
        return

    log_error("Gateway-Restart fehlgeschlagen")

# ========================
# TELEGRAM
# ========================
def send_telegram(message: str):
    load_env()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "") or os.environ.get("TELEGRAM_HOME_CHANNEL", "")

    if not bot_token or not chat_id:
        log("Telegram nicht konfiguriert — logge Nachricht statt zu senden")
        return

    import urllib.request
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }).encode()

    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log("Telegram gesendet")
    except Exception as e:
        log_error(f"Telegram fehlgeschlagen: {e}")

def format_telegram(selected: dict, change_count: int) -> str:
    now = datetime.now()
    date_str = now.strftime("%d.%m.%Y")
    time_str = now.strftime("%H:%M")

    header = "🏷️ DRY-RUN MODE\n" if DRY_RUN else ""
    footer = "\n⚠️ CONFIG NICHT GEÄNDERT\n   (DRY-RUN active)" if DRY_RUN else ""

    def ctx_str(ctx):
        if ctx >= 1_000_000:
            return f"{ctx//1_000_000}M"
        elif ctx >= 1000:
            return f"{ctx//1000}K"
        return str(ctx)

    main = selected.get("main", [{}])[0] if selected.get("main") else {}
    vision = selected.get("vision", [{}])[0] if selected.get("vision") else {}
    reasoning = selected.get("reasoning", [{}])[0] if selected.get("reasoning") else {}

    main_score = main.get("aggregate", 0)
    vision_score = vision.get("aggregate", 0) if vision else 0
    reasoning_score = reasoning.get("aggregate", 0) if reasoning else 0

    main_fallback = [m["id"] for m in selected.get("main", [])[1:4]]
    vision_fallback = [m["id"] for m in selected.get("vision", [])[1:4]]

    fallback_text = ""
    if main_fallback:
        joined = "`, `".join(main_fallback)
        fallback_text += f"\n   ↳ Fallback: `{joined}`"

    return f"""{header}━━━━━━━━━━━━━━━━━━━━━━━━
🌅 OpenRouter Model Update
📅 {date_str} — {time_str}
━━━━━━━━━━━━━━━━━━━━━━━━

🖥️ MAIN MODEL
   `{main.get('id', 'N/A')}`
   Context: {ctx_str(main.get('context_length', 0))} | Score: {main_score:.0f}{fallback_text}

🖼️ VISION
   `{vision.get('id', 'N/A')}`
   Context: {ctx_str(vision.get('context_length', 0))} | Score: {vision_score:.0f}

🧠 REASONING
   `{reasoning.get('id', 'N/A')}`
   Context: {ctx_str(reasoning.get('context_length', 0))} | Score: {reasoning_score:.0f}

📊 Changes: {change_count}
━━━━━━━━━━━━━━━━━━━━━━━━{footer}"""

# ========================
# HISTORY TRACKING
# ========================
def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}

def save_history(history: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def check_changes(selected: dict) -> int:
    history = load_history()
    changes = 0

    for cat in ["main", "vision", "reasoning"]:
        if not selected.get(cat):
            continue
        current_model = selected[cat][0]["id"]
        prev = history.get(cat, {}).get("current")
        if prev and prev != current_model:
            changes += 1
            log(f"  Model changed: {cat} = {prev} → {current_model}")

    return changes

def update_history(selected: dict):
    history = load_history()
    now = datetime.now().strftime("%Y-%m-%d")

    for cat in ["main", "vision", "reasoning"]:
        if not selected.get(cat):
            continue
        current_model = selected[cat][0]["id"]
        prev = history.get(cat, {}).get("current", "")

        history[cat] = {
            "current": current_model,
            "previous": prev,
            "history": history.get(cat, {}).get("history", []) + [{
                "date": now,
                "model": current_model,
                "change": prev != current_model and prev != ""
            }]
        }

    save_history(history)

# ========================
# FALLBACK CHAIN
# ========================
def save_fallback_chains(selected: dict):
    chains = {}
    for cat in ["main", "vision", "reasoning", "coding"]:
        if selected.get(cat):
            chains[cat] = [m["id"] for m in selected[cat][:5]]

    fallback_file = HERMES_HOME / ".model_fallback.json"
    with open(fallback_file, "w") as f:
        json.dump({
            "updated": datetime.now().isoformat(),
            "chains": chains
        }, f, indent=2)

    log(f"Fallback-Chains gespeichert: {fallback_file}")

# ========================
# MAIN
# ========================
def main():
    global DRY_RUN

    # Load .env FIRST before anything else
    load_env()

    DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
    log("=" * 50)
    log("OpenRouter Model Updater — START (V3)")
    log(f"DRY_RUN: {DRY_RUN}")
    log("=" * 50)

    api_key = get_api_key()
    if not api_key:
        log_warn("Kein OpenRouter API Key gefunden — Health-Checks übersprungen")
    else:
        log(f"API Key gefunden (length: {len(api_key)})")

    # Phase 1: Fetch
    models = fetch_free_models()

    # Phase 2: Score & Select
    log("Berechne Scores (weighted)...")
    selected = select_top_models(models, api_key)

    # Log Top-3 pro Kategorie
    for cat in ["main", "vision", "coding", "reasoning"]:
        top3 = selected.get(cat, [])[:3]
        log(f"\n  === TOP {cat.upper()} ===")
        for i, m in enumerate(top3):
            ctx = m.get("context_length", 0)
            agg = m.get("aggregate", 0)
            scores = m.get("dim_scores", {})
            log(f"    {i+1}. [{agg:.1f}] {m['id']}")
            log(f"       Context:{scores.get('context',0):.0f} Trending:{scores.get('trending',0):.0f} Feature:{scores.get('feature',0):.0f}")

    # Phase 3: Health-Check
    log("\nFühre Health-Checks durch...")
    selected_with_health = select_with_health_check(selected, api_key)

    # Phase 4: Config (nur wenn nicht Dry-Run)
    if not DRY_RUN:
        log("\nPatching config.yaml...")

        previous_models = get_current_models()
        log(f"Vorherige Modelle gespeichert: {previous_models}")

        backup_config()

        try:
            main_model = selected_with_health["main"][0]["id"] if selected_with_health.get("main") else None
            vision_model = selected_with_health["vision"][0]["id"] if selected_with_health.get("vision") else None
            delegation_model = main_model

            if main_model and vision_model:
                patch_config(main_model, vision_model, delegation_model)
                update_history(selected_with_health)
                save_fallback_chains(selected_with_health)
                log("Config erfolgreich aktualisiert")
            else:
                log_error("Keine Modelle ausgewählt — nichts geändert")
        except Exception as e:
            log_error(f"FEHLER während Update: {e}")
            log("Stelle vorherige Config wieder her...")
            restore_config(previous_models)
            raise
    elif not DRY_RUN and change_count == 0:
        log("\nKeine Änderungen — Config bleibt unverändert, kein Restart nötig")
        save_fallback_chains(selected_with_health)
    else:
        log("\nDRY-RUN: Config nicht geändert")
        save_fallback_chains(selected_with_health)

    # Phase 5: Changes check
    change_count = check_changes(selected_with_health)

    # Phase 6: Telegram (IMMER — VOR dem Restart damit die Nachricht sicher rauskommt)
    log("\nSende Telegram...")
    message = format_telegram(selected_with_health, change_count)
    send_telegram(message)

    # Phase 7: Gateway Restart (nachdem Telegram raus ist)
    if not DRY_RUN and change_count > 0:
        restart_gateway()

    # Save selection
    with open(SELECTION_FILE, "w") as f:
        json.dump({
            "updated": datetime.now().isoformat(),
            "dry_run": DRY_RUN,
            "selected": selected_with_health
        }, f, indent=2)

    log("\n" + "=" * 50)
    log("OpenRouter Model Updater — FERTIG")
    log("=" * 50)

if __name__ == "__main__":
    main()
