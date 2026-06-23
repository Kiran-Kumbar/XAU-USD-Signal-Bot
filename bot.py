import requests
import time
from datetime import datetime, date
import pytz

# ============================================
# CONFIGURATION
# ============================================
import os

TELEGRAM_TOKEN      = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY  = os.environ.get("TWELVEDATA_API_KEY")

SYMBOL = "XAU/USD"

# ⚠️ SET THIS TO YOUR ACTUAL CURRENT BALANCE — NOT YOUR ORIGINAL CAPITAL.
# Risk sizing is meaningless if this number is wrong.
CAPITAL = 100  # <-- CHANGE THIS to your real current balance before running

RISK_PERCENT       = 0.03          # 3% per trade (reduced from 5% given drawdown)
RISK_AMOUNT        = CAPITAL * RISK_PERCENT
MAX_TRADES_PER_DAY  = 2            # reduced from 3 — fewer, higher quality only
MIN_CONFIDENCE      = 80
IST = pytz.timezone('Asia/Kolkata')

# PAPER MODE: if True, bot only LOGS signals to Telegram with a clear
# "PAPER SIGNAL — NOT EXECUTED" label. No claim is made that any trade
# is placed or guaranteed to work. Recommended to leave True for now.
PAPER_MODE = True

# ============================================
# GOLD CONTRACT SPECS (IMPORTANT — DIFFERENT FROM FOREX)
# ============================================
# XAU/USD price is quoted in USD per troy ounce, e.g. 4200.50
# 1 "point" here = $0.01 move in price (we define point = 1 cent for granularity)
# Standard lot (1.00) = 100 oz → $1 move in price = $100 P&L per 1.00 lot
# So per 0.01 lot (micro), $1 move in price = $1 P&L
# Therefore: value per point (0.01 price move) per 0.01 lot ≈ $0.01 per point... 
# but most retail brokers quote gold in $0.01 increments and call a "point" = $0.01 move.
# We use the broker-standard convention below — VERIFY against your own broker's
# contract specification before trusting this in execution, as conventions vary.
GOLD_POINT_VALUE_PER_001_LOT = 0.01  # $ P&L per 1 point (0.01 USD) move per 0.01 lot

# ============================================
# STATE
# ============================================
trades_today = 0
last_trade_date = None
last_signal_direction = None
last_signal_time = 0

# ============================================
# TELEGRAM
# ============================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram sent: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ============================================
# FETCH CANDLES
# ============================================
def get_candles(interval="5min", outputsize=100):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON"
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"API Error: {data}")
            return None
        candles = []
        for c in reversed(data["values"]):
            candles.append({
                "time": c["datetime"],
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"])
            })
        return candles
    except Exception as e:
        print(f"Candle fetch error: {e}")
        return None

# ============================================
# SWING HIGH / LOW DETECTION
# ============================================
def find_swing_highs(candles, lookback=5):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        is_swing = all(
            candles[i]["high"] >= candles[i-j]["high"] and
            candles[i]["high"] >= candles[i+j]["high"]
            for j in range(1, lookback+1)
        )
        if is_swing:
            swings.append((i, candles[i]["high"]))
    return swings

def find_swing_lows(candles, lookback=5):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        is_swing = all(
            candles[i]["low"] <= candles[i-j]["low"] and
            candles[i]["low"] <= candles[i+j]["low"]
            for j in range(1, lookback+1)
        )
        if is_swing:
            swings.append((i, candles[i]["low"]))
    return swings

# ============================================
# MARKET STRUCTURE (BOS / CHoCH)
# ============================================
def analyze_structure(candles):
    if len(candles) < 30:
        return "NEUTRAL", 0

    swing_highs = find_swing_highs(candles[-50:], lookback=3)
    swing_lows  = find_swing_lows(candles[-50:],  lookback=3)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL", 0

    sh1, sh2 = swing_highs[-2][1], swing_highs[-1][1]
    sl1, sl2 = swing_lows[-2][1],  swing_lows[-1][1]

    bullish_score = 0
    bearish_score = 0

    if sh2 > sh1:
        bullish_score += 1
    else:
        bearish_score += 1

    if sl2 > sl1:
        bullish_score += 1
    else:
        bearish_score += 1

    recent_closes = [c["close"] for c in candles[-5:]]
    if recent_closes[-1] > recent_closes[0]:
        bullish_score += 1
    else:
        bearish_score += 1

    closes = [c["close"] for c in candles]
    ema_fast = sum(closes[-10:]) / 10
    ema_slow = sum(closes[-30:]) / 30

    if ema_fast > ema_slow:
        bullish_score += 1
    else:
        bearish_score += 1

    if bullish_score >= 3:
        return "BULLISH", bullish_score
    elif bearish_score >= 3:
        return "BEARISH", bearish_score
    else:
        return "NEUTRAL", 0

# ============================================
# LIQUIDITY GRAB DETECTION
# ============================================
def detect_liquidity_grab(candles):
    if len(candles) < 10:
        return None, 0

    recent = candles[-10:]
    prev   = candles[-2]
    curr   = candles[-1]

    recent_highs = [c["high"] for c in recent[:-2]]
    recent_lows  = [c["low"]  for c in recent[:-2]]

    if not recent_highs or not recent_lows:
        return None, 0

    max_high = max(recent_highs)
    min_low  = min(recent_lows)

    score = 0
    grab  = None

    if prev["high"] > max_high:
        wick_size = prev["high"] - max(prev["open"], prev["close"])
        body_size = abs(prev["open"] - prev["close"])
        if wick_size > body_size * 1.5:
            if curr["close"] < curr["open"]:
                grab  = "BEARISH_GRAB"
                score = 2

    if prev["low"] < min_low:
        wick_size = min(prev["open"], prev["close"]) - prev["low"]
        body_size = abs(prev["open"] - prev["close"])
        if wick_size > body_size * 1.5:
            if curr["close"] > curr["open"]:
                grab  = "BULLISH_GRAB"
                score = 2

    return grab, score

# ============================================
# SUPPORT & RESISTANCE (KEY LEVELS)
# ============================================
def get_sr_levels(candles_1h, candles_5m):
    h1_highs = sorted([c["high"] for c in candles_1h[-30:]], reverse=True)
    h1_lows  = sorted([c["low"]  for c in candles_1h[-30:]])

    resistance = h1_highs[2] if len(h1_highs) > 2 else h1_highs[0]
    support    = h1_lows[2]  if len(h1_lows)  > 2 else h1_lows[0]

    current = candles_5m[-1]["close"]

    # Gold moves in dollars, not the same scale as GBPJPY.
    # Distance expressed in actual $ price difference (not x1000).
    dist_resistance = abs(resistance - current)
    dist_support    = abs(current - support)

    # "Near" threshold for gold = $8 (tunable). Gold's average true range
    # on 5m is typically $3-8, so this keeps it comparable in spirit
    # to the 50-point GBPJPY threshold, scaled to gold's actual volatility.
    near_resistance = dist_resistance < 8
    near_support    = dist_support    < 8

    return support, resistance, near_support, near_resistance

# ============================================
# CANDLESTICK PATTERN (REJECTION)
# ============================================
def check_candle_pattern(candles):
    if len(candles) < 3:
        return None, 0

    c2 = candles[-2]
    c3 = candles[-1]

    score = 0
    pattern = None

    if (c2["open"] < c2["close"] and
        c3["open"] > c3["close"] and
        c3["open"] >= c2["close"] and
        c3["close"] <= c2["open"]):
        pattern = "BEARISH_ENGULF"
        score   = 2

    elif (c2["open"] > c2["close"] and
          c3["open"] < c3["close"] and
          c3["open"] <= c2["close"] and
          c3["close"] >= c2["open"]):
        pattern = "BULLISH_ENGULF"
        score   = 2

    elif (c3["high"] - max(c3["open"], c3["close"]) >
          2 * abs(c3["open"] - c3["close"])):
        pattern = "SHOOTING_STAR"
        score   = 1

    elif (min(c3["open"], c3["close"]) - c3["low"] >
          2 * abs(c3["open"] - c3["close"])):
        pattern = "HAMMER"
        score   = 1

    return pattern, score

# ============================================
# RSI
# ============================================
def calculate_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50

    closes = [c["close"] for c in candles[-(period+1):]]
    gains  = []
    losses = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

# ============================================
# FAIR VALUE GAP (FVG)
# ============================================
def detect_fvg(candles):
    if len(candles) < 3:
        return None, 0

    c1 = candles[-3]
    c3 = candles[-1]

    if c1["high"] < c3["low"]:
        return "BULLISH_FVG", 1

    if c1["low"] > c3["high"]:
        return "BEARISH_FVG", 1

    return None, 0

# ============================================
# CALCULATE LOTS — GOLD SPECIFIC, CAPITAL AWARE
# ============================================
def calculate_lots(sl_dollars):
    """
    sl_dollars: stop loss distance in actual USD price terms (e.g. 6.50 means $6.50 move)
    Returns lot size so that if SL is hit, loss ≈ RISK_AMOUNT (never more).
    """
    if sl_dollars <= 0:
        return 0.01

    # Points here = cents. $1 = 100 points (cents).
    sl_points = sl_dollars * 100

    if sl_points <= 0:
        return 0.01

    lots = RISK_AMOUNT / (sl_points * GOLD_POINT_VALUE_PER_001_LOT)
    # Convert to lot units (lots variable currently in units of 0.01 lot)
    lots = lots * 0.01
    lots = round(min(max(lots, 0.01), 0.50), 2)
    return lots

# ============================================
# MAIN SIGNAL ENGINE
# ============================================
def generate_signal(candles_5m, candles_1h):
    if not candles_5m or not candles_1h:
        return None

    current_price = candles_5m[-1]["close"]

    h1_structure, h1_score  = analyze_structure(candles_1h)
    m5_structure, m5_score  = analyze_structure(candles_5m)
    liquidity, liq_score    = detect_liquidity_grab(candles_5m)
    pattern,  pat_score     = check_candle_pattern(candles_5m)
    fvg,      fvg_score     = detect_fvg(candles_5m)
    support, resistance, near_support, near_resistance = get_sr_levels(candles_1h, candles_5m)
    rsi = calculate_rsi(candles_5m)

    print(f"\n--- ANALYSIS ---")
    print(f"Price: {current_price:.2f}")
    print(f"H1 Structure: {h1_structure} (score {h1_score})")
    print(f"M5 Structure: {m5_structure} (score {m5_score})")
    print(f"Liquidity: {liquidity}")
    print(f"Pattern: {pattern}")
    print(f"FVG: {fvg}")
    print(f"RSI: {rsi}")
    print(f"Support: {support:.2f} | Resistance: {resistance:.2f}")

    long_score = 0
    long_reasons = []

    if h1_structure == "BULLISH":
        long_score += 30
        long_reasons.append("H1 Bullish Structure")
    if m5_structure == "BULLISH":
        long_score += 15
        long_reasons.append("M5 Bullish Structure")
    if liquidity == "BULLISH_GRAB":
        long_score += 20
        long_reasons.append("Bullish Liquidity Grab")
    if pattern in ["BULLISH_ENGULF", "HAMMER"]:
        long_score += pat_score * 8
        long_reasons.append(f"Pattern: {pattern}")
    if fvg == "BULLISH_FVG":
        long_score += 10
        long_reasons.append("Bullish FVG")
    if near_support:
        long_score += 10
        long_reasons.append("Near Support Level")
    if rsi < 40:
        long_score += 10
        long_reasons.append(f"RSI Oversold ({rsi})")

    short_score = 0
    short_reasons = []

    if h1_structure == "BEARISH":
        short_score += 30
        short_reasons.append("H1 Bearish Structure")
    if m5_structure == "BEARISH":
        short_score += 15
        short_reasons.append("M5 Bearish Structure")
    if liquidity == "BEARISH_GRAB":
        short_score += 20
        short_reasons.append("Bearish Liquidity Grab")
    if pattern in ["BEARISH_ENGULF", "SHOOTING_STAR"]:
        short_score += pat_score * 8
        short_reasons.append(f"Pattern: {pattern}")
    if fvg == "BEARISH_FVG":
        short_score += 10
        short_reasons.append("Bearish FVG")
    if near_resistance:
        short_score += 10
        short_reasons.append("Near Resistance Level")
    if rsi > 60:
        short_score += 10
        short_reasons.append(f"RSI Overbought ({rsi})")

    print(f"Long Score: {long_score} | Short Score: {short_score}")

    signal    = None
    score     = 0
    reasons   = []
    sl_dollars = 0
    tp_dollars = 0

    # Minimum SL distance for gold on 5m/H1 confluence — gold whipsaws hard,
    # a too-tight SL here gets stop-hunted constantly. $4 floor is intentional.
    MIN_SL_DOLLARS = 4.0

    if long_score > short_score and long_score >= MIN_CONFIDENCE:
        signal     = "LONG"
        score      = min(long_score, 99)
        reasons    = long_reasons
        sl_dollars = max(current_price - support, MIN_SL_DOLLARS)
        tp_dollars = sl_dollars * 2

    elif short_score > long_score and short_score >= MIN_CONFIDENCE:
        signal     = "SHORT"
        score      = min(short_score, 99)
        reasons    = short_reasons
        sl_dollars = max(resistance - current_price, MIN_SL_DOLLARS)
        tp_dollars = sl_dollars * 2

    if not signal:
        print(f"No signal. Long: {long_score} Short: {short_score} (need {MIN_CONFIDENCE}+)")
        return None

    lots = calculate_lots(sl_dollars)
    potential_loss = sl_dollars * 100 * GOLD_POINT_VALUE_PER_001_LOT * (lots / 0.01)

    return {
        "signal":     signal,
        "price":      current_price,
        "sl_dollars": round(sl_dollars, 2),
        "tp_dollars": round(tp_dollars, 2),
        "lots":       lots,
        "potential_loss": round(potential_loss, 2),
        "confidence": score,
        "reasons":    reasons,
        "rsi":        rsi,
        "support":    support,
        "resistance": resistance,
        "h1_bias":    h1_structure,
        "pattern":    pattern or "None",
        "liquidity":  liquidity or "None",
        "fvg":        fvg or "None"
    }

# ============================================
# FORMAT & SEND SIGNAL
# ============================================
def send_signal(sig):
    direction = "🟢 LONG (BUY)" if sig["signal"] == "LONG" else "🔴 SHORT (SELL)"
    emoji     = "📈" if sig["signal"] == "LONG" else "📉"

    if sig["signal"] == "LONG":
        sl_price = sig["price"] - sig["sl_dollars"]
        tp_price = sig["price"] + sig["tp_dollars"]
    else:
        sl_price = sig["price"] + sig["sl_dollars"]
        tp_price = sig["price"] - sig["tp_dollars"]

    reasons_text = "\n".join([f"  ✅ {r}" for r in sig["reasons"]])
    now_ist      = datetime.now(IST).strftime('%d %b %Y %H:%M IST')

    mode_banner = (
        "🧪 <b>PAPER SIGNAL — NOT EXECUTED</b>\n"
        "<i>Logged for review only. No trade has been placed.</i>\n\n"
        if PAPER_MODE else
        "⚠️ <b>LIVE SIGNAL</b>\n\n"
    )

    msg = f"""
{mode_banner}⚔️ <b>XAUUSD SIGNAL</b> {emoji}
━━━━━━━━━━━━━━━━━━━━

📊 <b>Direction:</b> {direction}
💰 <b>Entry:</b> {sig["price"]:.2f} (Market Now)
🛑 <b>Stop Loss:</b> {sl_price:.2f} (${sig["sl_dollars"]:.2f} away)
🎯 <b>Take Profit:</b> {tp_price:.2f} (${sig["tp_dollars"]:.2f} away)
📦 <b>Lots:</b> {sig["lots"]}
⚖️ <b>R:R:</b> 1:2
🎯 <b>Confidence:</b> {sig["confidence"]}%
💵 <b>Max Loss If SL Hit:</b> ~${sig["potential_loss"]:.2f}
   (Target: {RISK_PERCENT*100:.0f}% of ${CAPITAL} capital = ${RISK_AMOUNT:.2f})

━━━━━━━━━━━━━━━━━━━━
📋 <b>WHY THIS SIGNAL:</b>
{reasons_text}

📈 <b>H1 Bias:</b> {sig["h1_bias"]}
🕯 <b>Pattern:</b> {sig["pattern"]}
💧 <b>Liquidity:</b> {sig["liquidity"]}
📊 <b>FVG:</b> {sig["fvg"]}
📉 <b>RSI:</b> {sig["rsi"]}
🔴 <b>Resistance:</b> {sig["resistance"]:.2f}
🟢 <b>Support:</b> {sig["support"]:.2f}

━━━━━━━━━━━━━━━━━━━━
🕐 {now_ist}
⚠️ <i>Verify your broker's actual spread and contract size before
acting on this. Gold spreads vary widely by broker. Max {MAX_TRADES_PER_DAY} signals/day.</i>
"""
    send_telegram(msg)
    print(f"✅ Signal sent: {sig['signal']} @ {sig['price']} | Confidence: {sig['confidence']}% | Max loss: ${sig['potential_loss']:.2f}")

# ============================================
# SESSION LABEL
# ============================================
def get_session():
    now  = datetime.now(IST)
    hour = now.hour

    if 5 <= hour < 9:
        return "Asian Session 🌏"
    elif 13 <= hour < 18:
        return "London Session 🇬🇧"
    elif 18 <= hour < 23:
        return "New York Session 🗽 (best for gold)"
    else:
        return "Off Hours 🌙"

# ============================================
# MAIN LOOP
# ============================================
def main():
    global trades_today, last_trade_date, last_signal_direction, last_signal_time

    print("🚀 XAUUSD Bot Starting...")
    print(f"CAPITAL set to: ${CAPITAL} | Risk per trade: ${RISK_AMOUNT:.2f} ({RISK_PERCENT*100:.0f}%)")
    print(f"PAPER_MODE: {PAPER_MODE}")

    send_telegram(
        f"🚀 <b>XAUUSD Signal Bot is LIVE!</b>\n\n"
        f"{'🧪 <b>PAPER MODE — signals are logged only, not executed</b>' if PAPER_MODE else '⚠️ <b>LIVE MODE</b>'}\n\n"
        "⚙️ <b>Settings:</b>\n"
        f"• Capital basis: ${CAPITAL}\n"
        f"• Risk per trade: ${RISK_AMOUNT:.2f} ({RISK_PERCENT*100:.0f}%)\n"
        f"• Min Confidence: {MIN_CONFIDENCE}%+\n"
        f"• Max Signals/Day: {MAX_TRADES_PER_DAY}\n"
        "• Strategy: Structure + Liquidity + S&R + FVG + RSI\n\n"
        "Scanning every 5 minutes... ⚔️"
    )

    while True:
        try:
            now       = datetime.now(IST)
            today     = date.today()
            session   = get_session()

            if last_trade_date != today:
                trades_today      = 0
                last_trade_date   = today
                last_signal_direction = None
                print(f"\n📅 New day: {today} — Counter reset")
                send_telegram(f"📅 <b>New Day: {today}</b>\nSignals remaining: {MAX_TRADES_PER_DAY}")

            print(f"\n[{now.strftime('%H:%M')}] {session} | Signals today: {trades_today}/{MAX_TRADES_PER_DAY}")

            if trades_today >= MAX_TRADES_PER_DAY:
                print("Max signals reached for today. Sleeping 1 hour...")
                time.sleep(3600)
                continue

            # Gold has thin liquidity on weekends (futures roll, etc).
            # Skipping Sat/Sun like forex as a safety default — adjust
            # if your broker offers weekend gold CFDs you trust.
            if now.weekday() >= 5:
                print("Weekend — skipping by default. Sleeping 1 hour...")
                time.sleep(3600)
                continue

            print("Fetching candles...")
            candles_5m = get_candles("5min", 100)
            time.sleep(2)
            candles_1h = get_candles("1h", 100)

            if not candles_5m or not candles_1h:
                print("Failed to fetch candles. Retry in 2 min...")
                time.sleep(120)
                continue

            sig = generate_signal(candles_5m, candles_1h)

            if sig:
                current_time = time.time()
                time_since_last = current_time - last_signal_time

                if (last_signal_direction == sig["signal"] and
                        time_since_last < 2700):
                    print("Same direction signal within 45min. Skipping...")
                else:
                    send_signal(sig)
                    trades_today          += 1
                    last_signal_time       = current_time
                    last_signal_direction  = sig["signal"]
                    print(f"Signals today: {trades_today}/{MAX_TRADES_PER_DAY}")

                    if trades_today >= MAX_TRADES_PER_DAY:
                        send_telegram(
                            f"🔴 <b>Daily Signal Limit Reached!</b>\n"
                            f"{MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY} signals sent today.\n"
                            f"Bot resumes tomorrow."
                        )
            else:
                print("No high confidence signal. Waiting...")

            time.sleep(300)

        except KeyboardInterrupt:
            print("Bot stopped manually.")
            send_telegram("🔴 <b>Bot stopped manually.</b>")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(120)

if __name__ == "__main__":
    main()