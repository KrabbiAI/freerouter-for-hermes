#!/bin/bash
# OpenRouter Model Updater — Installer
# Führt dieses Script aus: bash install.sh

set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPT_DIR="$HERMES_HOME/scripts"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================"
echo "  OpenRouter Model Updater — Installation"
echo "============================================"
echo ""

# 1. Prüfe ob Hermes Agent installiert ist
if [ ! -d "$HERMES_HOME" ]; then
    echo "❌ Hermes Home nicht gefunden: $HERMES_HOME"
    echo "   Installiere Hermes Agent first: https://github.com/NousResearch/hermes-agent"
    exit 1
fi
echo "✅ Hermes Home: $HERMES_HOME"

# 2. Prüfe ob .env existiert
if [ ! -f "$HERMES_HOME/.env" ]; then
    echo "⚠️  .env nicht gefunden — kopiere Template"
    cp "$REPO_DIR/templates/.env.template" "$HERMES_HOME/.env"
    echo "   → Bitte .env editieren und API Keys setzen!"
fi

# 3. Prüfe ob OPENROUTER_API_KEY gesetzt ist
if ! grep -q "OPENROUTER_API_KEY=sk-or-v1" "$HERMES_HOME/.env" 2>/dev/null; then
    echo "⚠️  OPENROUTER_API_KEY nicht in .env gefunden!"
    echo "   Bitte setze den Key in $HERMES_HOME/.env"
    echo "   Format: OPENROUTER_API_KEY=sk-or-v1-..."
    exit 1
fi
echo "✅ OpenRouter API Key gefunden"

# 4. Prüfe ob config.yaml existiert
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
    echo "⚠️  config.yaml nicht gefunden — kopiere Template"
    cp "$REPO_DIR/templates/config.yaml.template" "$HERMES_HOME/config.yaml"
    echo "   → Bitte config.yaml anpassen"
fi
echo "✅ Config: $HERMES_HOME/config.yaml"

# 5. Kopiere Script
mkdir -p "$SCRIPT_DIR"
cp "$REPO_DIR/scripts/openrouter_model_updater.py" "$SCRIPT_DIR/"
chmod +x "$SCRIPT_DIR/openrouter_model_updater.py"
echo "✅ Script installiert: $SCRIPT_DIR/openrouter_model_updater.py"

# 6. Testlauf (Dry-Run)
echo ""
echo "============================================"
echo "  Testlauf (Dry-Run)..."
echo "============================================"
cd "$HERMES_HOME"
DRY_RUN=true python3 "$SCRIPT_DIR/openrouter_model_updater.py" 2>&1 || {
    echo "❌ Testlauf fehlgeschlagen — prüfe die Konfiguration"
    exit 1
}

echo ""
echo "============================================"
echo "  Installation erfolgreich! ✅"
echo "============================================"
echo ""
echo "Nächste Schritte:"
echo ""
echo "  1. Testlauf (Dry-Run):"
echo "     DRY_RUN=true python3 $SCRIPT_DIR/openrouter_model_updater.py"
echo ""
echo "  2. Live-Run (ohne Dry-Run):"
echo "     DRY_RUN=false python3 $SCRIPT_DIR/openrouter_model_updater.py"
echo ""
echo "  3. Cron-Job einrichten (täglich um 06:00):"
echo "     hermes cron create '0 6 * * *' \\"
echo "       --prompt 'Führe das OpenRouter Model Updater Script aus (OHNE Dry-Run). Das Script: $SCRIPT_DIR/openrouter_model_updater.py' \\"
echo "       --name 'OpenRouter Model Updater' \\"
echo "       --toolsets terminal"
echo ""
echo "  4. ODER: Cron-Job manuell hinzufügen:"
echo "     crontab -e"
echo "     0 6 * * * cd $HERMES_HOME && DRY_RUN=false HERMES_HOME=$HERMES_HOME python3 $SCRIPT_DIR/openrouter_model_updater.py >> $HERMES_HOME/logs/model_updater_cron.log 2>&1"
echo ""
