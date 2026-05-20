# 🔄 OpenRouter Free Model Updater

Automatisches Update-System für [Hermes Agent](https://github.com/NousResearch/hermes-agent) — wählt die besten kostenlosen OpenRouter-Modelle aus und aktualisiert die Konfiguration.

## Features

- **Weighted Scoring** — Bewertet Modelle nach Context-Length, Popularität, Features, Speed und Qualität
- **Health-Checks** — Testet Modelle vor der Auswahl mit echten API-Calls
- **Failure Tracking** — Banned Modelle die 3x fehlschlagen für 24h
- **Fallback-Chains** — Speichert Top-5 Modelle pro Kategorie als Backup
- **Telegram-Benachrichtigungen** — Optional bei jedem Lauf
- **Dry-Run Modus** — Testen ohne Config-Änderungen
- **Idempotent** — Keine Änderungen wenn sich nichts verschoben hat

## Voraussetzungen

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installiert
- [OpenRouter API Key](https://openrouter.ai/keys) (kostenlos)
- Python 3.10+
- Telegram Bot Token (optional, für Benachrichtigungen)

## Schnellstart

```bash
# 1. Repo klonen
git clone https://github.com/DEIN_USER/openrouter-model-updater.git
cd openrouter-model-updater

# 2. Installer ausführen
bash install.sh

# 3. API Key setzen (falls nicht schon in ~/.hermes/.env)
nano ~/.hermes/.env
# → OPENROUTER_API_KEY=sk-or-v1-DEIN_KEY

# 4. Testlauf
DRY_RUN=true python3 scripts/openrouter_model_updater.py

# 5. Live-Run
DRY_RUN=false python3 scripts/openrouter_model_updater.py
```

## Cron-Job einrichten

### Option A: Hermes CLI (empfohlen)
```bash
hermes cron create '0 6 * * *' \
  --prompt 'Führe das OpenRouter Model Updater Script aus (OHNE Dry-Run). Das Script: ~/.hermes/scripts/openrouter_model_updater.py' \
  --name 'OpenRouter Model Updater' \
  --toolsets terminal
```

### Option B: System crontab
```bash
crontab -e
# Diese Zeile hinzufügen:
0 6 * * * cd $HOME/.hermes && DRY_RUN=false python3 scripts/openrouter_model_updater.py >> logs/model_updater_cron.log 2>&1
```

## Konfiguration

### Environment Variables

| Variable | Required | Beschreibung |
|----------|----------|--------------|
| `OPENROUTER_API_KEY` | ✅ | OpenRouter API Key |
| `TELEGRAM_BOT_TOKEN` | ❌ | Telegram Bot Token für Benachrichtigungen |
| `TELEGRAM_CHAT_ID` | ❌ | Telegram Chat ID |
| `DRY_RUN` | ❌ | `true` = Testlauf ohne Config-Änderungen (default: `true`) |
| `HERMES_HOME` | ❌ | Pfad zu Hermes Home (default: `~/.hermes`) |

### Scoring-Gewichtung

| Dimension | Gewichtung | Beschreibung |
|-----------|------------|--------------|
| Context | 20% | Context-Length (32k=0, 1M+=100) |
| Trending | 25% | Popularität/Ranking |
| Feature | 30% | Multimodal, Tools, Coding, Vision |
| Speed | 10% | Preis (free=100) |
| Quality | 15% | Kombination aus Context + Modality + Tools |

### Quality Gates

- Mindestens 128k Context-Length
- Muss kostenlos sein (prompt price = 0)
- Nicht gebanned (weniger als 3 Fehler in 24h)

## Ablauf

```
1. Fetch      → Lädt alle Free Models von OpenRouter API
2. Score      → Berechnet gewichteten Score für jedes Model
3. Select     → Wählt Top-5 pro Kategorie (main, vision, coding, reasoning)
4. Health     → Testet Top-3 per API-Call
5. Config     → Patches config.yaml (nur bei Änderungen)
6. Telegram   → Sendet Benachrichtigung
7. Restart    → Hermes Gateway Restart (nur bei Änderungen)
```

## Dateien

```
openrouter-model-updater/
├── scripts/
│   └── openrouter_model_updater.py   # Hauptscript
├── templates/
│   ├── .env.template                 # .env Template
│   └── config.yaml.template          # config.yaml Template
├── install.sh                        # Installer
└── README.md                         # Diese Datei
```

## Troubleshooting

### "OPENROUTER_API_KEY nicht gefunden"
→ Key in `~/.hermes/.env` setzen: `OPENROUTER_API_KEY=sk-or-v1-...`

### "Gateway-Restart fehlgeschlagen"
→ Manuell neu starten: `hermes gateway restart`

### "Health-Check failed"
→ Model ist temporär down — wird automatisch gebanned und beim nächsten Lauf ausgelassen

### Telegram-Nachricht nicht erhalten
→ `TELEGRAM_BOT_TOKEN` und `TELEGRAM_CHAT_ID` in `.env` setzen

## Lizenz

MIT
