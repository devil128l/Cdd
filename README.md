# 📄 README.md

```markdown
# 🎮 Discord Quest Completer Bot

> A powerful Discord bot that automatically completes Discord quests with a Premium/Free tier system.

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)
![Discord](https://img.shields.io/badge/Discord.py-2.3+-purple?style=for-the-badge&logo=discord)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

---

## 📑 Table of Contents

- [Features](#-features)
- [Tier System](#-tier-system)
- [Commands](#-commands)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Bot Setup](#-bot-setup-on-discord)
- [Usage](#-usage)
- [Project Structure](#-project-structure)
- [FAQ](#-faq)
- [Disclaimer](#-disclaimer)

---

## ✨ Features

- 🚀 **Auto Quest Completion** — Completes Discord quests via heartbeat automation
- 📊 **Real-Time Progress** — Live progress bar with ETA updates
- 👑 **Premium System** — Role-based unlimited access for premium members
- 🆓 **Free Tier** — All users can complete quests once every 24 hours
- 🔑 **Secure Token Storage** — Tokens can only be set via DMs
- 📋 **Quest Viewer** — View all available quests and their details
- ⚙️ **Admin Controls** — Full server configuration via slash commands
- 📝 **Activity Logging** — Log all bot actions to a dedicated channel
- 🛑 **Task Control** — Stop running quests at any time

---

## 🏆 Tier System

| Feature | 🆓 Free Users | 👑 Premium Users |
|---------|--------------|------------------|
| `/quests` | ✅ Unlimited | ✅ Unlimited |
| `/questinfo` | ✅ Unlimited | ✅ Unlimited |
| `/complete` | ⏳ Once per 24h | ✅ No Cooldown |
| `/completeall` | ⏳ Once per 24h | ✅ No Cooldown |
| `/stop` | ✅ Available | ✅ Available |
| `/checktoken` | ✅ Available | ✅ Available |

> **Premium** is granted by assigning a specific Discord role to the user. Admins can set this role using `/setpremiumrole`.

---

## 📌 Commands

### 🔑 Token Management

| Command | Description | Where |
|---------|-------------|-------|
| `/settoken <token>` | Save your Discord user token | **DM Only** |
| `/removetoken` | Delete your stored token | Anywhere |
| `/checktoken` | Verify if your token is valid | Anywhere |

### 🎮 Quest Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/quests` | List all available quests on your account | Everyone |
| `/questinfo <number>` | View detailed info about a specific quest | Everyone |
| `/complete <number>` | Start completing a specific quest | Free: 24h CD / Premium: None |
| `/completeall` | Complete all available quests one by one | Free: 24h CD / Premium: None |
| `/stop` | Stop your currently running quest | Everyone |

### ⚙️ Admin Commands

| Command | Description | Permission |
|---------|-------------|------------|
| `/setpremiumrole <role>` | Set the role for premium access | Administrator |
| `/setlogchannel <channel>` | Set the channel for activity logs | Administrator |
| `/setcooldown <hours>` | Change free user cooldown (default: 24) | Administrator |
| `/resetcooldown <user>` | Reset a specific user's cooldown | Administrator |
| `/settings` | View current bot configuration | Administrator |

### ℹ️ General

| Command | Description |
|---------|-------------|
| `/help` | View all commands and bot info |

---

## 🛠️ Installation

### Prerequisites

- **Python 3.10** or higher
- **pip** (Python package manager)
- A **Discord Bot Token** ([create one here](https://discord.com/developers/applications))

### Step 1: Clone / Download

```bash
git clone https://github.com/yourusername/quest-completer-bot.git
cd quest-completer-bot
```

### Step 2: Install Dependencies

```bash
pip install discord.py httpx
```

### Step 3: Create Data Directory

```bash
mkdir data
```

### Step 4: Configure

Edit `config.json` with your bot token (see [Configuration](#-configuration)).

### Step 5: Run

```bash
python bot.py
```

---

## ⚙️ Configuration

### `config.json`

```json
{
    "BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
    "PREMIUM_ROLE_ID": 0,
    "LOG_CHANNEL_ID": 0,
    "EMBED_COLOR": 5763719,
    "OWNER_IDS": [],
    "FREE_COOLDOWN_HOURS": 24
}
```

| Field | Type | Description |
|-------|------|-------------|
| `BOT_TOKEN` | string | Your Discord bot token from Developer Portal |
| `PREMIUM_ROLE_ID` | integer | Discord role ID for premium members (0 = disabled) |
| `LOG_CHANNEL_ID` | integer | Channel ID for bot activity logs (0 = disabled) |
| `EMBED_COLOR` | integer | Default embed color (decimal) |
| `OWNER_IDS` | list | List of user IDs with owner privileges |
| `FREE_COOLDOWN_HOURS` | integer | Cooldown hours for free users (default: 24) |

### Data Files (Auto-Generated)

| File | Description |
|------|-------------|
| `data/tokens.json` | Stores user Discord tokens (encrypted by user ID) |
| `data/cooldowns.json` | Tracks free user cooldown timestamps |
| `data/premium.json` | Reserved for future premium data |

---

## 🤖 Bot Setup on Discord

### 1. Create Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"**
3. Give it a name (e.g., "Quest Completer")
4. Click **"Create"**

### 2. Create Bot

1. Go to the **"Bot"** tab
2. Click **"Add Bot"**
3. Under **"Privileged Gateway Intents"**, enable:
   - ✅ **MESSAGE CONTENT INTENT**
   - ✅ **SERVER MEMBERS INTENT**
   - ✅ **PRESENCE INTENT**
4. Click **"Reset Token"** and copy your bot token
5. Paste it into `config.json` → `BOT_TOKEN`

### 3. Invite Bot to Server

Use this URL (replace `YOUR_CLIENT_ID`):

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=8&scope=bot%20applications.commands
```

> **Note:** `permissions=8` gives Administrator. You can customize permissions as needed.

### 4. Set Premium Role

1. Create a role in your server (e.g., "Quest Premium")
2. Use the command: `/setpremiumrole @Quest Premium`
3. Assign this role to premium members

### 5. Set Log Channel (Optional)

1. Create a channel (e.g., `#bot-logs`)
2. Use the command: `/setlogchannel #bot-logs`

---

## 📖 Usage

### For Users

```
1. Open DMs with the bot
2. Type: /settoken YOUR_DISCORD_TOKEN
3. Go to your server
4. Type: /quests          → See available quests
5. Type: /complete 1       → Start completing quest #1
6. Watch the progress bar update in real-time!
7. Quest complete → You'll get a notification
```

### How to Get Your Discord Token

```
1. Open Discord in your web browser (discord.com/app)
2. Press F12 to open Developer Tools
3. Go to the "Console" tab
4. Paste this code:

   (webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()

5. Copy the token that appears
6. Send it to the bot via DM: /settoken <your_token>
```

> ⚠️ **NEVER share your token publicly or send it in a server channel!**

### For Admins

```
1. /setpremiumrole @VIP     → Set premium role
2. /setlogchannel #logs      → Set log channel
3. /setcooldown 12           → Change free cooldown to 12h
4. /settings                 → View all current settings
5. /resetcooldown @user      → Reset someone's cooldown
```

---

## 📁 Project Structure

```
quest-completer-bot/
├── bot.py                  # Main bot file (all logic)
├── config.json             # Bot configuration
├── README.md               # This file
├── requirements.txt        # Python dependencies
└── data/                   # Auto-generated data folder
    ├── tokens.json         # User tokens storage
    ├── cooldowns.json      # Cooldown tracking
    └── premium.json        # Premium data (future)
```

---

## ❓ FAQ

### Q: Is this safe to use?
**A:** The bot stores tokens locally on your machine. However, using user tokens (self-botting) is against Discord's Terms of Service. Use at your own risk with an alt account.

### Q: Why does my token expire?
**A:** Discord tokens can expire when you change your password, log out of all devices, or if Discord detects suspicious activity. Simply use `/settoken` again with your new token.

### Q: Can free users bypass the cooldown?
**A:** No. The cooldown is enforced server-side by the bot. Free users must wait 24 hours (or whatever the admin sets) between quest completions.

### Q: What happens if I stop a quest mid-way?
**A:** Use `/stop` to cancel. The progress made so far may or may not count depending on how much time elapsed. You can restart the quest later.

### Q: How do I give someone premium?
**A:** Assign them the role you set with `/setpremiumrole`. They'll instantly get unlimited quest completions.

### Q: Can the bot run 24/7?
**A:** Yes! You can host it on a VPS (like DigitalOcean, Hetzner) or use free hosting platforms like Railway, Render, or Replit.

---

## ⚠️ Disclaimer

> **IMPORTANT:** This bot uses Discord user tokens to interact with the Discord API on behalf of users. This practice (commonly known as "self-botting") is **against Discord's Terms of Service**.
>
> - 🚫 Using this on your main account may result in account termination
> - 🚫 The developers are not responsible for any actions taken by Discord
> - ✅ This project is for **educational purposes only**
> - ✅ Always use an **alternate account** if you choose to use this
>
> **By using this bot, you acknowledge and accept all risks involved.**

---

## 📄 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

---

## 💬 Support

If you have questions or need help:

- Open an **Issue** on GitHub
- Join our **Discord Server** (link here)
- Contact the developer directly

---

<p align="center">
  Made with ❤️ for the Discord community
</p>
```

---

## `requirements.txt` (Bonus)

```
discord.py>=2.3.0
httpx>=0.27.0
```

---
