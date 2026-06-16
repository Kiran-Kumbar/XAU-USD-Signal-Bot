import requests
import time
from datetime import datetime, date
import pytz

# ============================================
# CONFIGURATION
# ============================================
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")
SYMBOL = "GBP/JPY"
CAPITAL = 200
RISK_PERCENT = 0.05        # 5% = $10
RISK_AMOUNT = CAPITAL * RISK_PERCENT
MAX_TRADES_PER_DAY = 3
MIN_CONFIDENCE = 80        # Only 80%+ signals
IST = pytz.timezone('Asia/Kolkata')

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

    # Last 2 swing highs and lows
    sh1, sh2 = swing_highs[-2][1], swing_highs[-1][1]
    sl1, sl2 = swing_lows[-2][1],  swing_lows[-1][1]

    bullish_score = 0
    bearish_score = 0

    # Higher Highs = bullish
    if sh2 > sh1:
        bullish_score += 1
    else:
        bearish_score += 1

    # Higher Lows = bullish
    if sl2 > sl1:
        bullish_score += 1
    else:
        bearish_score += 1

    # Recent close direction
    recent_closes = [c["close"] for c in candles[-5:]]
    if recent_closes[-1] > recent_closes[0]:
        bullish_score += 1
    else:
        bearish_score += 1

    # EMA cross approximation (fast vs slow)
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

    # Bearish grab: spike above high, close back below
    if prev["high"] > max_high:
        wick_size = prev["high"] - max(prev["open"], prev["close"])
        body_size = abs(prev["open"] - prev["close"])
        if wick_size > body_size * 1.5:  # Wick 1.5x bigger than body
            if curr["close"] < curr["open"]:  # Next candle bearish
                grab  = "BEARISH_GRAB"
                score = 2

    # Bullish grab: spike below low, close back above
    if prev["low"] < min_low:
        wick_size = min(prev["open"], prev["close"]) - prev["low"]
        body_size = abs(prev["open"] - prev["close"])
        if wick_size > body_size * 1.5:
            if curr["close"] > curr["open"]:  # Next candle bullish
                grab  = "BULLISH_GRAB"
                score = 2

    return grab, score

# ============================================
# SUPPORT & RESISTANCE (KEY LEVELS)
# ============================================
def get_sr_levels(candles_1h, candles_5m):
    # 1H levels (stronger)
    h1_highs = sorted([c["high"] for c in candles_1h[-30:]], reverse=True)
    h1_lows  = sorted([c["low"]  for c in candles_1h[-30:]])

    resistance = h1_highs[2] if len(h1_highs) > 2 else h1_highs[0]
    support    = h1_lows[2]  if len(h1_lows)  > 2 else h1_lows[0]

    current = candles_5m[-1]["close"]

    # Distance from S&R
    dist_resistance = abs(resistance - current) / current * 1000
    dist_support    = abs(current - support)    / current * 1000

    near_resistance = dist_resistance < 50   # Within 50 points
    near_support    = dist_support    < 50

    return support, resistance, near_support, near_resistance

# ============================================
# CANDLESTICK PATTERN (REJECTION)
# ============================================
def check_candle_pattern(candles):
    if len(candles) < 3:
        return None, 0

    c1 = candles[-3]
    c2 = candles[-2]
    c3 = candles[-1]

    score = 0
    pattern = None

    # Bearish engulfing
    if (c2["open"] < c2["close"] and   # c2 bullish
        c3["open"] > c3["close"] and   # c3 bearish
        c3["open"] >= c2["close"] and
        c3["close"] <= c2["open"]):
        pattern = "BEARISH_ENGULF"
        score   = 2

    # Bullish engulfing
    elif (c2["open"] > c2["close"] and  # c2 bearish
          c3["open"] < c3["close"] and  # c3 bullish
          c3["open"] <= c2["close"] and
          c3["close"] >= c2["open"]):
        pattern = "BULLISH_ENGULF"
        score   = 2

    # Shooting star (bearish)
    elif (c3["high"] - max(c3["open"], c3["close"]) >
          2 * abs(c3["open"] - c3["close"])):
        pattern = "SHOOTING_STAR"
        score   = 1

    # Hammer (bullish)
    elif (min(c3["open"], c3["close"]) - c3["low"] >
          2 * abs(c3["open"] - c3["close"])):
        pattern = "HAMMER"
        score   = 1

    return pattern, score

# ============================================
# RSI APPROXIMATION
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
    c2 = candles[-2]
    c3 = candles[-1]

    # Bullish FVG: c1 high < c3 low (gap between c1 and c3)
    if c1["high"] < c3["low"]:
        return "BULLISH_FVG", 1

    # Bearish FVG: c1 low > c3 high
    if c1["low"] > c3["high"]:
        return "BEARISH_FVG", 1

    return None, 0

# ============================================
# CALCULATE LOTS
# ============================================
def calculate_lots(sl_points):
    if sl_points <= 0:
        return 0.01
    # GBP/JPY: ~$0.09 per point per 0.01 lot
    pip_value = 0.09
    lots = RISK_AMOUNT / (sl_points * pip_value)
    lots = round(min(max(lots, 0.01), 0.50), 2)
    return lots

# ============================================
# MAIN SIGNAL ENGINE
# ============================================
def generate_signal(candles_5m, candles_1h):
    if not candles_5m or not candles_1h:
        return None

    current_price = candles_5m[-1]["close"]

    # --- ANALYSIS LAYERS ---
    h1_structure, h1_score  = analyze_structure(candles_1h)
    m5_structure, m5_score  = analyze_structure(candles_5m)
    liquidity, liq_score    = detect_liquidity_grab(candles_5m)
    pattern,  pat_score     = check_candle_pattern(candles_5m)
    fvg,      fvg_score     = detect_fvg(candles_5m)
    support, resistance, near_support, near_resistance = get_sr_levels(candles_1h, candles_5m)
    rsi = calculate_rsi(candles_5m)

    print(f"\n--- ANALYSIS ---")
    print(f"Price: {current_price:.3f}")
    print(f"H1 Structure: {h1_structure} (score {h1_score})")
    print(f"M5 Structure: {m5_structure} (score {m5_score})")
    print(f"Liquidity: {liquidity}")
    print(f"Pattern: {pattern}")
    print(f"FVG: {fvg}")
    print(f"RSI: {rsi}")
    print(f"Support: {support:.3f} | Resistance: {resistance:.3f}")

    # ----------------------------------------
    # LONG CONDITIONS (SCORED)
    # ----------------------------------------
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

    # ----------------------------------------
    # SHORT CONDITIONS (SCORED)
    # ----------------------------------------
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

    # ----------------------------------------
    # DETERMINE SIGNAL
    # ----------------------------------------
    signal    = None
    score     = 0
    reasons   = []
    sl_points = 0
    tp_points = 0

    if long_score > short_score and long_score >= MIN_CONFIDENCE:
        signal    = "LONG"
        score     = min(long_score, 99)
        reasons   = long_reasons
        sl_points = max(int((current_price - support) * 1000) + 30, 80)
        tp_points = sl_points * 2

    elif short_score > long_score and short_score >= MIN_CONFIDENCE:
        signal    = "SHORT"
        score     = min(short_score, 99)
        reasons   = short_reasons
        sl_points = max(int((resistance - current_price) * 1000) + 30, 80)
        tp_points = sl_points * 2

    if not signal:
        print(f"No signal. Long: {long_score} Short: {short_score} (need {MIN_CONFIDENCE}+)")
        return None

    lots = calculate_lots(sl_points)

    return {
        "signal":     signal,
        "price":      current_price,
        "sl_points":  sl_points,
        "tp_points":  tp_points,
        "lots":       lots,
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
        sl_price = sig["price"] - (sig["sl_points"] / 1000)
        tp_price = sig["price"] + (sig["tp_points"] / 1000)
    else:
        sl_price = sig["price"] + (sig["sl_points"] / 1000)
        tp_price = sig["price"] - (sig["tp_points"] / 1000)

    reasons_text = "\n".join([f"  ✅ {r}" for r in sig["reasons"]])
    now_ist      = datetime.now(IST).strftime('%d %b %Y %H:%M IST')

    msg = f"""
⚔️ <b>GBPJPY SIGNAL</b> {emoji}
━━━━━━━━━━━━━━━━━━━━

📊 <b>Direction:</b> {direction}
💰 <b>Entry:</b> {sig["price"]:.3f} (Market Now)
🛑 <b>Stop Loss:</b> {sl_price:.3f} ({sig["sl_points"]} pts)
🎯 <b>Take Profit:</b> {tp_price:.3f} ({sig["tp_points"]} pts)
📦 <b>Lots:</b> {sig["lots"]}
⚖️ <b>R:R:</b> 1:2
🎯 <b>Confidence:</b> {sig["confidence"]}%
💵 <b>Risk:</b> $10 (5% of $200)

━━━━━━━━━━━━━━━━━━━━
📋 <b>WHY THIS TRADE:</b>
{reasons_text}

📈 <b>H1 Bias:</b> {sig["h1_bias"]}
🕯 <b>Pattern:</b> {sig["pattern"]}
💧 <b>Liquidity:</b> {sig["liquidity"]}
📊 <b>FVG:</b> {sig["fvg"]}
📉 <b>RSI:</b> {sig["rsi"]}
🔴 <b>Resistance:</b> {sig["resistance"]:.3f}
🟢 <b>Support:</b> {sig["support"]:.3f}

━━━━━━━━━━━━━━━━━━━━
🕐 {now_ist}
⚠️ <i>Verify before executing. Max 3 trades/day.</i>
"""
    send_telegram(msg)
    print(f"✅ Signal sent: {sig['signal']} @ {sig['price']} | Confidence: {sig['confidence']}%")

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
        return "New York Session 🗽"
    else:
        return "Off Hours 🌙"

# ============================================
# MAIN LOOP
# ============================================
def main():
    global trades_today, last_trade_date, last_signal_direction, last_signal_time

    print("🚀 GBPJPY Bot Starting...")
    send_telegram(
        "🚀 <b>GBPJPY Signal Bot is LIVE!</b>\n\n"
        "⚙️ <b>Settings:</b>\n"
        "• Sessions: All 24/7\n"
        "• Min Confidence: 80%+\n"
        "• Max Trades/Day: 3\n"
        "• Risk: $10 per trade (5%)\n"
        "• Strategy: Structure + Liquidity + S&R + FVG + RSI\n\n"
        "Scanning every 5 minutes... ⚔️"
    )

    while True:
        try:
            now       = datetime.now(IST)
            today     = date.today()
            session   = get_session()

            # Reset daily trade counter
            if last_trade_date != today:
                trades_today      = 0
                last_trade_date   = today
                last_signal_direction = None
                print(f"\n📅 New day: {today} — Trade counter reset")
                send_telegram(f"📅 <b>New Trading Day: {today}</b>\nTrades remaining: {MAX_TRADES_PER_DAY}")

            print(f"\n[{now.strftime('%H:%M')}] {session} | Trades today: {trades_today}/{MAX_TRADES_PER_DAY}")

            # Max trades reached
            if trades_today >= MAX_TRADES_PER_DAY:
                print("Max trades reached for today. Sleeping 1 hour...")
                time.sleep(3600)
                continue

            # Skip weekends (Sat=5, Sun=6)
            if now.weekday() >= 5:
                print("Weekend — market closed. Sleeping 1 hour...")
                time.sleep(3600)
                continue

            # Fetch candles
            print("Fetching candles...")
            candles_5m = get_candles("5min", 100)
            time.sleep(2)
            candles_1h = get_candles("1h",   100)

            if not candles_5m or not candles_1h:
                print("Failed to fetch candles. Retry in 2 min...")
                time.sleep(120)
                continue

            # Generate signal
            sig = generate_signal(candles_5m, candles_1h)

            if sig:
                current_time = time.time()
                time_since_last = current_time - last_signal_time

                # Avoid same direction signal within 45 minutes
                if (last_signal_direction == sig["signal"] and
                        time_since_last < 2700):
                    print(f"Same direction signal within 45min. Skipping...")
                else:
                    send_signal(sig)
                    trades_today          += 1
                    last_signal_time       = current_time
                    last_signal_direction  = sig["signal"]
                    print(f"Trades today: {trades_today}/{MAX_TRADES_PER_DAY}")

                    if trades_today >= MAX_TRADES_PER_DAY:
                        send_telegram(
                            f"🔴 <b>Daily Trade Limit Reached!</b>\n"
                            f"3/3 trades sent today.\n"
                            f"Bot resumes tomorrow. Rest well. ⚔️"
                        )
            else:
                print("No high confidence signal. Waiting...")

            # Scan every 5 minutes
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
