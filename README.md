# XAUUSD Signal Bot

An automated Gold (XAU/USD) trading signal bot that analyzes the market and sends high-probability trade setups directly to your Telegram. 

## Features

- **Symbol:** XAU/USD (Gold)
- **Timeframes:** Analyzes 1H (for overall structure and bias) and 5M (for entry triggers).
- **Advanced Strategy:**
  - Market Structure Analysis (BOS / CHoCH)
  - Key Support & Resistance Levels
  - Liquidity Grabs
  - Fair Value Gaps (FVG)
  - Candlestick Rejection Patterns (Engulfing, Pin Bars)
  - RSI (Overbought/Oversold conditions)
- **Risk Management:** Dynamic lot sizing based on your specified capital and risk percentage.
- **Telegram Integration:** Sends detailed signal alerts including Entry, Stop Loss, Take Profit, and the logic behind the trade.
- **Paper Trading Mode:** Test the bot safely by logging signals without actual execution.

## Configuration

Set the following environment variables:

- `TELEGRAM_TOKEN`: Your Telegram Bot API token.
- `TELEGRAM_CHAT_ID`: The ID of the Telegram chat/channel to send signals to.
- `TWELVEDATA_API_KEY`: Your TwelveData API key for market data.

Inside `bot.py`, you can tweak:
- `CAPITAL`: Your current account balance (default: 100).
- `RISK_PERCENT`: Risk per trade (default: 3%).
- `MAX_TRADES_PER_DAY`: Daily trade limit (default: 2).
- `MIN_CONFIDENCE`: Minimum signal confidence score to alert (default: 80).
- `PAPER_MODE`: Set to `True` for paper signals, or `False` for live execution formatting.

## Deployment

This bot is ready to be deployed on platforms like Railway or Heroku. A `Procfile` and `railway.json` are included.

### Run Locally

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the bot:
   ```bash
   python bot.py
   ```
