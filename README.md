<div align="center">

# 🥇 XAU/USD (Gold) Precision Signal Bot

[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Telegram API](https://img.shields.io/badge/Telegram-API-0088CC?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![TwelveData](https://img.shields.io/badge/TwelveData-Market_Data-FF4B4B?style=for-the-badge)](https://twelvedata.com/)
[![Railway](https://img.shields.io/badge/Railway-Deploy-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](https://railway.app/)

*An institutional-grade algorithmic trading bot engineered specifically for Gold (XAU/USD). Utilizes Smart Money Concepts (SMC) including Market Structure, Liquidity Grabs, and Fair Value Gaps (FVG) to dispatch high-probability trade setups directly to Telegram.*

</div>

---

## 📑 Table of Contents
- [About the Project](#-about-the-project)
- [Algorithmic Strategy](#-algorithmic-strategy)
- [Key Features](#-key-features)
- [Requirements & Setup](#-requirements--setup)
- [Environment Variables](#-environment-variables)
- [Deployment (Railway/Heroku)](#-deployment)
- [Disclaimer](#-disclaimer)

---

## 🧠 About the Project

This bot monitors the **XAU/USD (Gold)** forex market autonomously 24/5. Rather than relying on lagging indicators, it implements core **Smart Money Concepts (SMC)** logic. It processes `1-Hour` candles to determine the macro bias and `5-Minute` candles to isolate surgical entry triggers. 

When a confluence of signals aligns with strict risk-management parameters, it formats a comprehensive report detailing the entry, exact stop loss, take profit, and the precise reasoning behind the trade—delivered instantly to your Telegram.

---

## ⚙️ Algorithmic Strategy

The bot scores potential trade setups out of 100 based on multiple confluences:

1. **Market Structure (BOS / CHoCH)**: Analyzes the last 50 candles to map swing highs and lows.
2. **Liquidity Grabs**: Identifies aggressive wick rejections beyond recent extremes.
3. **Fair Value Gaps (FVG)**: Detects impulsive momentum and structural imbalances.
4. **Key S&R Levels**: Calculates dynamic Support and Resistance zones.
5. **Price Action Patterns**: Engulfing candles, Hammers, and Shooting Stars.
6. **RSI Divergence**: Uses standard 14-period RSI to validate overbought/oversold extremes.

---

## ✨ Key Features

- **Dynamic Lot Sizing**: Strictly adheres to a predefined risk profile (e.g., exactly 3% of capital per trade).
- **Capital Adequacy Checks**: Gold is volatile. The bot mathematically verifies if your account balance can handle the minimum lot size for a specific setup's stop-loss. If the risk is too high, it dynamically rejects the trade.
- **Paper Trading Mode**: Safety-first testing. Generates "Paper Signals" for forward-testing without risking live capital.
- **Session Awareness**: Operates aware of Asian, London, and New York overlapping sessions.

---

## 🛠️ Requirements & Setup

### Prerequisites
- Python 3.9+
- A Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- A TwelveData API Key (for real-time XAU/USD data)

### Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YourUsername/GJsignal-bot.git
   cd GJsignal-bot
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the script:**
   Inside `bot.py`, locate the Configuration block and update your risk settings:
   ```python
   CAPITAL = 100               # Your exact real balance
   RISK_PERCENT = 0.03         # 3% risk per trade
   MAX_TRADES_PER_DAY = 2      # Quality over quantity
   MIN_CONFIDENCE = 80         # Strict 80%+ confluence required
   PAPER_MODE = True           # Set False ONLY when ready for live execution
   ```

4. **Run the bot:**
   ```bash
   python bot.py
   ```

---

## 🔑 Environment Variables

To keep your credentials secure, do **not** hardcode them. Export these environment variables locally or input them into your hosting provider's dashboard:

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | The HTTP API token from BotFather. |
| `TELEGRAM_CHAT_ID` | Your personal ID or Channel ID where signals will be sent. |
| `TWELVEDATA_API_KEY` | Free API key from TwelveData to pull candlestick data. |

---

## 🚀 Deployment

The project is fully pre-configured for seamless cloud deployment to ensure 24/5 uptime.

### Railway.app (Recommended)
This repository includes a `railway.json` and `Procfile`. 
1. Link your GitHub repository to a new Railway project.
2. Add your Environment Variables in the Railway Dashboard.
3. The bot will deploy and run automatically.

---

## ⚠️ Disclaimer

**Educational Purposes Only.** Trading Gold (XAU/USD) involves significant risk of loss and is not suitable for all investors. The algorithms provided in this repository do not constitute financial advice. Always use `PAPER_MODE` before engaging in live markets. Past performance does not guarantee future results.

---
<div align="center">
  <i>May your Stop Losses be tight and your Take Profits be hit.</i>
</div>
