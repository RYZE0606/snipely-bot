# Snipely 🎯
**Vinted Deals & Sniper Telegram Bot**

Snipely is a highly efficient, multi-user Telegram bot designed to monitor Vinted for new listings and deals. Built with Python, it features an interactive UI and is fully Dockerized for seamless deployment on any server environment.

## ✨ Features
- **Multi-User & Admin Management:** Easily manage user access, set monitor limits, and control subscription expiry dates.
- **Interactive Setup Wizard:** Create, edit, and delete monitors directly within Telegram using inline buttons.
- **Live Alerts & Deal History:** Get instant notifications with photos, prices, seller ratings, and a history of recent deals.
- **Snooze / Pause Functionality:** Users can temporarily pause their monitor alerts for specific timeframes.
- **Docker-Ready Structure:** Uses relative data directories and persistent volumes to ensure configuration data is always safe.

## 🚀 Getting Started

### Prerequisites
- [Docker](https://www.docker.com/) and Docker Compose installed on your host machine.
- A valid Telegram Bot Token.

### Installation

**1. Clone the repository:**
```bash
git clone https://github.com/RYZE0606/snipely-bot
cd snipely-bot
```

**2. Prepare the Data Directory:**
The bot requires a local `data` folder to store persistent JSON files (`monitors.json`, `users.json`, etc.). Create this folder in the root directory:
```bash
mkdir data
```
*(Note: The contents of the `data` folder are ignored by Git to protect user privacy. If you are migrating from a local environment, place your existing JSON files in this folder).*

**3. Deploy with Docker:**
Build the image and start the bot in the background:
```bash
docker compose up -d
```

## 🔄 Updating the Bot

When pushing new code updates to GitHub, you can easily update your live server without losing any user data:
```bash
git pull
docker compose up -d --build
```

---

## ⚖️ Ownership & Copyright

**© 2026 RYZE0606. All rights reserved.**

This software and its source code are the intellectual property of RYZE0606. Unauthorized copying, distribution, modification, or commercial use of this project, via any medium, is strictly prohibited without explicit permission.
