# Telegram Job Monitor & Multi-Platform Freelance Pipeline

A Telegram-controlled bot that schedules platform scans every 15 minutes, processes fresh job matches, and sends ranked Telegram updates to each subscribed user chat.

## Features

- **Core bundle**: Discord + Reddit run together as the primary workflow
- **Optional add-ons**: Wellfound, Upwork, and Freelancer.com can be enabled separately
- **Telegram controlled**: Start and stop scheduled platform scans, run manual scans, and manage profiles from Telegram
- **On-demand AI proposals**: Generate a custom proposal only when you press the Telegram button
- **Strict relevance filtering**: Sends only software engineering, web development, and full stack jobs
- **Multi-user profiles**: Stores a separate name, portfolio, GitHub, rate, and skills per Telegram user
- **Rich Telegram cards**: Includes posting date, relevance rating, description, and key job fields
- **SQLite-backed schedules**: Restores active chat/platform schedules and dedupe state after a bot restart
- **Persistent API logs**: Writes structured response logs under `logs/api/`
- **Run locking**: Prevents the same chat/platform job from executing multiple times at once

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Schedule the core bundle: Discord + Reddit |
| `/start core` | Schedule Discord + Reddit together |
| `/scan` | Run subscribed platforms now, or the core bundle if nothing is subscribed |
| `/scan core` | Run Discord + Reddit once immediately |
| `/stop core` | Stop the core bundle |
| `/start all` | Schedule all supported platforms |
| `/start discord` | Schedule Discord history scans every 15 minutes |
| `/start reddit` | Schedule Reddit updates every 15 minutes |
| `/start wellfound` | Schedule Wellfound updates every 15 minutes |
| `/start upwork` | Schedule Upwork updates every 15 minutes |
| `/start freelancer` | Schedule Freelancer.com updates every 15 minutes |
| `/stop [platform]` | Stop a specific scheduled platform |
| `/stop all` | Stop all scheduled platforms for the current chat |
| `/scan reddit` | Run one platform once immediately |
| `/status` | Show active schedules and last run state |
| `/users` | List registered users |
| `/profile` | View your saved profile |
| `/set name Your Name` | Set your display name |
| `/set github https://github.com/you` | Set your GitHub URL |
| `/set portfolio https://yoursite.com` | Set your portfolio URL |
| `/set rate $20-30/hr` | Set your hourly rate |
| `/set skills React, Node, Python` | Set your skills |
| `/help` | Show command help |

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd jobber
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Required values:

- `DISCORD_TOKEN`: Personal Discord token used for gateway and API access
- `DISCORD_SERVER_IDS`: Comma-separated Discord server IDs to monitor
- `GROQ_API_KEY`: Groq API key for classification and proposal generation
- `TELEGRAM_BOT_TOKEN`: Telegram bot token from `@BotFather`

Optional values:

- `GROQ_MODEL`: Defaults to `llama-3.3-70b-versatile`
- `MIN_MESSAGE_LENGTH`
- `PREFILTER_KEYWORDS`
- `LOG_LEVEL`
- `RECONNECT_DELAY_SECONDS`
- `MAX_RECONNECT_ATTEMPTS`
- `TELEGRAM_COOLDOWN_SECONDS`
- `SCHEDULE_INTERVAL_SECONDS`
- `SCHEDULE_DB_PATH`

### 3. Run

```bash
# Full Telegram-controlled bot
.venv/bin/python3 bot.py

# Web dashboard for scheduler, API status, and logs
.venv/bin/python3 dashboard_server.py --host 127.0.0.1 --port 8787

# Discord-only live monitor
.venv/bin/python3 main.py

# Reddit + Wellfound polling pipeline
.venv/bin/python3 pipeline.py

# One-time Discord history scan
.venv/bin/python3 fetch_recent.py
```

## Logs And State

- `schedule_state.db` stores active chat/platform schedules, dedupe state, and active run locks
- `logs/monitor.log` stores application logs
- `logs/api/*.jsonl` stores persistent structured API response logs
- `dashboard_server.py` serves a local UI for status, API health, and log viewing

## Tests

```bash
python3 -m unittest discover -s tests
```

## Project Layout

```text
bot.py              - Telegram-controlled scheduler and delivery pipeline
dashboard_server.py - Local dashboard web server
dashboard_data.py   - Dashboard data aggregation from SQLite and logs
main.py             - Standalone Discord live monitor
pipeline.py         - Standalone Reddit + Wellfound polling pipeline
mass_apply.py       - One-time mass scan plus proposal generation
fetch_recent.py     - Scan the last 24 hours of Discord messages
config.py           - Environment loading and runtime config
schedule_store.py   - Persistent scheduler state and dedupe storage
api_logger.py       - Structured API response logging
discord_gateway.py  - Discord Gateway client
classifier.py       - Groq-based job classification
prefilter.py        - Keyword pre-filter before AI classification
notifier.py         - Telegram Bot API notifications
auto_apply.py       - Proposal generation and Discord DM sending
profiles.py         - Per-user Telegram profile storage
models.py           - Shared data models
dashboard/
  index.html        - Dashboard shell
  app.js            - Dashboard client logic
  styles.css        - Dashboard styling
platforms/
  base.py           - Shared platform job model
  reddit.py         - Reddit monitor
  wellfound.py      - Wellfound monitor
  upwork.py         - Upwork RSS monitor
  freelancer_api.py - Freelancer.com API monitor
  discord_history.py - Scheduled Discord history fetcher
  indeed.py         - Indeed fetcher
  dice.py           - Dice fetcher
```

## Platforms Monitored

| Platform | Method | Default cadence |
|---|---|---|
| Discord | History scan / gateway | Every 15 minutes in core bundle |
| Reddit | JSON API | Every 15 minutes in core bundle |
| Wellfound | HTML scraping | Every 2 minutes |
| Upwork | RSS feeds | Every 2 minutes |
| Freelancer | Public API | Every 2 minutes |

## Tech Stack

- Python 3.13+
- `aiohttp`
- Groq Python SDK
- Telegram Bot API
- Discord Gateway v10

## License

MIT
