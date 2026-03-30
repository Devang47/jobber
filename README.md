# Discord Job Monitor & Multi-Platform Freelance Pipeline

A WhatsApp-controlled bot that monitors multiple platforms for freelance developer jobs and sends personalized proposals to your phone — ready to copy and apply.

## Features

- **5 Platforms**: Discord, Reddit, Wellfound, Upwork, Freelancer.com
- **WhatsApp Controlled**: Start/stop platforms, scan jobs, manage profiles — all from WhatsApp
- **AI Proposals**: Auto-generates personalized, human-sounding application messages using Groq (Llama 3.1)
- **Smart Scoring**: Jobs ranked by skill match (🔥🔥🔥 = perfect fit)
- **Multi-User**: Multiple people can use the bot with their own profiles (name, GitHub, portfolio, skills)
- **Copyable Messages**: Proposals sent as separate messages for easy copy-paste
- **User Targeting**: Route alerts to specific users by name
- **24/7 Background Service**: Runs as macOS Launch Agent, survives terminal close and reboots

## WhatsApp Commands

| Command | Description |
|---|---|
| `start reddit` | Start monitoring Reddit (12 dev subreddits) |
| `start wellfound` | Start monitoring Wellfound startup jobs |
| `start discord` | Start monitoring 9 Discord freelance servers |
| `start upwork` | Start monitoring Upwork RSS feeds |
| `start freelancer` | Start monitoring Freelancer.com API |
| `start all` | Start all platforms |
| `stop [platform]` | Stop a specific platform |
| `stop all` | Stop everything |
| `scan` | One-time scan, send to everyone |
| `scan Manas` | Scan and send only to Manas |
| `alert Dev` | Route live alerts to Dev only |
| `alert all` | Send alerts to everyone (default) |
| `status` | Show which platforms are running |
| `users` | List registered users |
| `profile` | View your profile |
| `set name Your Name` | Set your display name |
| `set github https://github.com/you` | Set your GitHub |
| `set portfolio https://yoursite.com` | Set your portfolio |
| `set rate $20-30/hr` | Set your hourly rate |
| `set skills React, Node, Python` | Set your skills |
| `help` | Show all commands |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/singhalmanas23/discord-job-monitor.git
cd discord-job-monitor
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Configure `.env`

```bash
cp .env.example .env
```

Fill in:
- **DISCORD_TOKEN**: Your personal Discord token (browser DevTools > Network tab > Authorization header)
- **GROQ_API_KEY**: Free API key from [console.groq.com](https://console.groq.com)
- **GREENAPI_INSTANCE_ID** + **GREENAPI_TOKEN**: Free from [green-api.com](https://green-api.com) (scan QR code with WhatsApp)
- **WHATSAPP_PHONE_NUMBERS**: Comma-separated numbers to receive alerts

### 3. Enable Green API notifications

Go to Green API dashboard and enable:
- Incoming webhook: **yes**
- Outgoing message webhook: **yes**

### 4. Run

```bash
# Interactive
.venv/bin/python3 bot.py

# Background (survives terminal close)
nohup .venv/bin/python3 bot.py > logs/bot.log 2>&1 &
```

### 5. macOS Background Service (optional, auto-starts on boot)

```bash
# Copy the plist (edit paths if needed)
cp com.manas.jobbot.plist ~/Library/LaunchAgents/

# Start
launchctl load ~/Library/LaunchAgents/com.manas.jobbot.plist

# Stop
launchctl unload ~/Library/LaunchAgents/com.manas.jobbot.plist

# Check logs
tail -f logs/bot.err.log
```

## Architecture

```
bot.py              — Main WhatsApp-controlled bot (entry point)
main.py             — Standalone Discord monitor
pipeline.py         — Standalone Reddit + Wellfound pipeline
mass_apply.py       — One-time mass scan + apply
fetch_recent.py     — Fetch last 24h from Discord servers
config.py           — .env loader
discord_gateway.py  — Discord WebSocket gateway client
classifier.py       — Groq AI job classification
prefilter.py        — Keyword pre-filter (reduces API calls)
notifier.py         — WhatsApp notifications via Green API
auto_apply.py       — Auto-generate proposals + Discord DM
profiles.py         — Per-user profile management
models.py           — Data models
logger_setup.py     — Logging config
platforms/
  base.py           — Platform job data model
  reddit.py         — Reddit monitor (12 subreddits)
  wellfound.py      — Wellfound/AngelList monitor
  upwork.py         — Upwork RSS feed monitor
  freelancer_api.py — Freelancer.com API monitor
  indeed.py         — Indeed scraper
  dice.py           — Dice API monitor
```

## Platforms Monitored

| Platform | Method | Poll Rate |
|---|---|---|
| Discord | WebSocket (real-time) | Instant |
| Reddit | JSON API (12 subreddits) | Every 2 min |
| Wellfound | HTML scraping | Every 2 min |
| Upwork | RSS feeds | Every 2 min |
| Freelancer | Public API | Every 2 min |

## Tech Stack

- Python 3.13+
- aiohttp (async HTTP + WebSocket)
- Groq API (Llama 3.1 8B for proposals)
- Green API (WhatsApp integration)
- Discord Gateway v10

## License

MIT
