import os
import requests
import time
from datetime import datetime, date
import pytz

# ============================================
# CONFIGURATION
# ============================================
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")

SYMBOL       = "XAU/USD"
CAPITAL      = 200
RISK_PERCENT = 0.05
RISK_AMOUNT  = CAPITAL * RISK_PERCENT   # $10

MAX_TRADES_PER_DAY = 3
MIN_CONFIDENCE     = 65
MIN_SL_DOLLARS     = 3.0
MAX_SL_DOLLARS     = 20.0
MIN_FVG_SIZE       = 1.50   # minimum FVG gap in dollars to count
EQUAL_LEVEL_TOL    = 0.30   # dollars — how close two highs/lows must be to count as "equal"
IST                = pytz.timezone('Asia/Kolkata')

PAPER_MODE = True

# Gold contract: 1.00 lot = 100oz → $1 move = $100/lot → $1 move = $1 per 0.01 lot
USD_PER_DOLLAR_MOVE_PER_001_LOT = 1.0

# ============================================
# STATE
# ============================================
trades_today          = 0
last_trade_date       = None
last_signal_direction = None
last_signal_time      = 0

# ============================================
# TELEGRAM
# ============================================
def send_telegram(message):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ============================================
# FETCH CANDLES
# ============================================
def get_candles(interval="5min", outputsize=100):
    url    = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL, "interval": interval,
        "outputsize": outputsize, "apikey": TWELVEDATA_API_KEY, "format": "JSON"
    }
    try:
        r    = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"API Error: {data}")
            return None
        candles = []
        for c in reversed(data["values"]):
            candles.append({
                "time": c["datetime"], "open": float(c["open"]),
                "high": float(c["high"]), "low": float(c["low"]),
                "close": float(c["close"])
            })
        return candles
    except Exception as e:
        print(f"Candle fetch error: {e}")
        return None

# ============================================
# SWING HIGH / LOW DETECTION
# ============================================
def find_swing_highs(candles, lookback=3):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        if all(candles[i]["high"] >= candles[i-j]["high"] and
               candles[i]["high"] >= candles[i+j]["high"]
               for j in range(1, lookback+1)):
            swings.append((i, candles[i]["high"]))
    return swings

def find_swing_lows(candles, lookback=3):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        if all(candles[i]["low"] <= candles[i-j]["low"] and
               candles[i]["low"] <= candles[i+j]["low"]
               for j in range(1, lookback+1)):
            swings.append((i, candles[i]["low"]))
    return swings

# ============================================
# MARKET STRUCTURE — BOS & CHoCH
# ============================================
def analyze_structure_bos(candles):
    """
    Returns (bias, bos_type, choch)
    bias    = "BULLISH" | "BEARISH" | "NEUTRAL"
    bos_type = "BOS" | "CHoCH" | None
    """
    if len(candles) < 20:
        return "NEUTRAL", None, False

    swing_highs = find_swing_highs(candles[-60:], lookback=3)
    swing_lows  = find_swing_lows(candles[-60:],  lookback=3)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL", None, False

    curr_price = candles[-1]["close"]
    last_sh    = swing_highs[-1][1]
    last_sl    = swing_lows[-1][1]
    prev_sh    = swing_highs[-2][1]
    prev_sl    = swing_lows[-2][1]

    # EMA trend bias
    closes   = [c["close"] for c in candles]
    ema_fast = sum(closes[-10:]) / 10
    ema_slow = sum(closes[-30:]) / 30
    ema_bias = "BULLISH" if ema_fast > ema_slow else "BEARISH"

    # BOS: price breaks above last swing high = bullish BOS
    #      price breaks below last swing low  = bearish BOS
    bullish_bos = curr_price > last_sh
    bearish_bos = curr_price < last_sl

    # CHoCH: structural reversal
    # Bullish CHoCH: was bearish trend (lower highs), now breaks above last swing high
    was_bearish  = last_sh < prev_sh and last_sl < prev_sl
    was_bullish  = last_sh > prev_sh and last_sl > prev_sl
    bullish_choch = was_bearish and bullish_bos
    bearish_choch = was_bullish and bearish_bos

    if bullish_choch:
        return "BULLISH", "CHoCH", True
    elif bearish_choch:
        return "BEARISH", "CHoCH", True
    elif bullish_bos and ema_bias == "BULLISH" and last_sh > prev_sh:
        return "BULLISH", "BOS", False
    elif bearish_bos and ema_bias == "BEARISH" and last_sl < prev_sl:
        return "BEARISH", "BOS", False
    elif ema_bias == "BULLISH" and last_sh > prev_sh and last_sl > prev_sl:
        return "BULLISH", None, False
    elif ema_bias == "BEARISH" and last_sh < prev_sh and last_sl < prev_sl:
        return "BEARISH", None, False
    else:
        return "NEUTRAL", None, False

# ============================================
# ORDER BLOCK DETECTION
# ============================================
def detect_order_block(candles, bias):
    """
    Bullish OB: last bearish (red) candle before a strong bullish impulse
    Bearish OB: last bullish (green) candle before a strong bearish impulse
    Returns (ob_high, ob_low, price_in_ob) or (None, None, False)
    """
    if len(candles) < 10:
        return None, None, False

    curr_price = candles[-1]["close"]

    if bias == "BULLISH":
        # Find last bearish candle followed by bullish impulse
        for i in range(len(candles)-3, max(len(candles)-20, 3), -1):
            c = candles[i]
            # Bearish candle
            if c["close"] < c["open"]:
                # Check if next 2 candles moved up strongly (impulse)
                next_closes = [candles[i+j]["close"] for j in range(1, 3) if i+j < len(candles)]
                if next_closes and max(next_closes) > c["high"]:
                    ob_high = c["high"]
                    ob_low  = c["low"]
                    price_in_ob = ob_low <= curr_price <= ob_high
                    return ob_high, ob_low, price_in_ob

    elif bias == "BEARISH":
        # Find last bullish candle followed by bearish impulse
        for i in range(len(candles)-3, max(len(candles)-20, 3), -1):
            c = candles[i]
            # Bullish candle
            if c["close"] > c["open"]:
                # Check if next 2 candles moved down strongly (impulse)
                next_closes = [candles[i+j]["close"] for j in range(1, 3) if i+j < len(candles)]
                if next_closes and min(next_closes) < c["low"]:
                    ob_high = c["high"]
                    ob_low  = c["low"]
                    price_in_ob = ob_low <= curr_price <= ob_high
                    return ob_high, ob_low, price_in_ob

    return None, None, False

# ============================================
# EQUAL HIGHS / EQUAL LOWS (LIQUIDITY ZONES)
# ============================================
def detect_liquidity_zones(candles):
    """
    Equal highs = buy-side liquidity (stops sitting above)
    Equal lows  = sell-side liquidity (stops sitting below)
    Returns (equal_highs, equal_lows, swept_high, swept_low)
    """
    if len(candles) < 10:
        return False, False, False, False

    recent      = candles[-30:]
    curr_price  = candles[-1]["close"]
    highs       = [c["high"] for c in recent[:-2]]
    lows        = [c["low"]  for c in recent[:-2]]
    last_high   = candles[-1]["high"]
    last_low    = candles[-1]["low"]

    # Find equal highs (within tolerance)
    equal_highs = False
    swept_high  = False
    for i in range(len(highs)):
        for j in range(i+1, len(highs)):
            if abs(highs[i] - highs[j]) <= EQUAL_LEVEL_TOL:
                equal_highs = True
                # Swept = current candle wick went above those equal highs
                if last_high > max(highs[i], highs[j]):
                    swept_high = True

    # Find equal lows (within tolerance)
    equal_lows = False
    swept_low  = False
    for i in range(len(lows)):
        for j in range(i+1, len(lows)):
            if abs(lows[i] - lows[j]) <= EQUAL_LEVEL_TOL:
                equal_lows = True
                if last_low < min(lows[i], lows[j]):
                    swept_low = True

    return equal_highs, equal_lows, swept_high, swept_low

# ============================================
# IMPROVED FVG WITH SIZE FILTER
# ============================================
def detect_fvg(candles):
    """
    Checks last 10 candle triplets for any unfilled FVG
    with minimum size filter to remove noise.
    """
    if len(candles) < 3:
        return None, 0

    # Check multiple recent candle triplets, not just the last 3
    for i in range(len(candles)-3, max(len(candles)-10, 0), -1):
        c1 = candles[i]
        c2 = candles[i+1]
        c3 = candles[i+2]

        # Bullish FVG: gap between c1 high and c3 low
        if c1["high"] < c3["low"]:
            gap_size = c3["low"] - c1["high"]
            if gap_size >= MIN_FVG_SIZE:
                # Check if still unfilled (current price hasn't gone back into gap)
                curr = candles[-1]["close"]
                if curr > c1["high"]:  # price still above the gap
                    return "BULLISH_FVG", 1

        # Bearish FVG: gap between c1 low and c3 high
        if c1["low"] > c3["high"]:
            gap_size = c1["low"] - c3["high"]
            if gap_size >= MIN_FVG_SIZE:
                curr = candles[-1]["close"]
                if curr < c1["low"]:  # price still below the gap
                    return "BEARISH_FVG", 1

    return None, 0

# ============================================
# IMPROVED LIQUIDITY GRAB
# ============================================
def detect_liquidity_grab(candles):
    """
    Spike above recent high then close back below = bearish grab
    Spike below recent low then close back above  = bullish grab
    Uses dollar-based wick size instead of body ratio
    to handle doji candles correctly.
    """
    if len(candles) < 10:
        return None, 0

    recent       = candles[-10:]
    prev         = candles[-2]
    curr         = candles[-1]
    recent_highs = [c["high"] for c in recent[:-2]]
    recent_lows  = [c["low"]  for c in recent[:-2]]

    if not recent_highs or not recent_lows:
        return None, 0

    max_high = max(recent_highs)
    min_low  = min(recent_lows)
    grab     = None
    score    = 0

    # Bearish grab: wick above recent high, closed back below
    if prev["high"] > max_high:
        wick_size = prev["high"] - max(prev["open"], prev["close"])
        if wick_size >= 1.0:  # minimum $1 wick to count
            if curr["close"] < curr["open"]:
                grab  = "BEARISH_GRAB"
                score = 2

    # Bullish grab: wick below recent low, closed back above
    if prev["low"] < min_low:
        wick_size = min(prev["open"], prev["close"]) - prev["low"]
        if wick_size >= 1.0:  # minimum $1 wick to count
            if curr["close"] > curr["open"]:
                grab  = "BULLISH_GRAB"
                score = 2

    return grab, score

# ============================================
# CANDLESTICK PATTERN
# ============================================
def check_candle_pattern(candles):
    if len(candles) < 2:
        return None, 0

    c2 = candles[-2]
    c3 = candles[-1]

    if (c2["open"] < c2["close"] and c3["open"] > c3["close"] and
            c3["open"] >= c2["close"] and c3["close"] <= c2["open"]):
        return "BEARISH_ENGULF", 2
    elif (c2["open"] > c2["close"] and c3["open"] < c3["close"] and
            c3["open"] <= c2["close"] and c3["close"] >= c2["open"]):
        return "BULLISH_ENGULF", 2
    elif (c3["high"] - max(c3["open"], c3["close"]) >
            2 * abs(c3["open"] - c3["close"]) and
            abs(c3["open"] - c3["close"]) > 0):
        return "SHOOTING_STAR", 1
    elif (min(c3["open"], c3["close"]) - c3["low"] >
            2 * abs(c3["open"] - c3["close"]) and
            abs(c3["open"] - c3["close"]) > 0):
        return "HAMMER", 1

    return None, 0

# ============================================
# RSI
# ============================================
def calculate_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50
    closes   = [c["close"] for c in candles[-(period+1):]]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100
    return round(100 - (100 / (1 + avg_g / avg_l)), 2)

# ============================================
# SMART SL PLACEMENT
# ============================================
def calculate_sl_distance(candles, bias, ob_high, ob_low):
    """
    LONG: SL below order block low, or below last swing low
    SHORT: SL above order block high, or above last swing high
    Returns sl_dollars (distance from current price)
    """
    curr = candles[-1]["close"]

    if bias == "LONG":
        if ob_low is not None:
            sl_level = ob_low - 0.50   # just below OB low
        else:
            swing_lows = find_swing_lows(candles[-30:])
            sl_level = swing_lows[-1][1] - 0.50 if swing_lows else curr - 8.0
        return max(curr - sl_level, MIN_SL_DOLLARS)

    elif bias == "SHORT":
        if ob_high is not None:
            sl_level = ob_high + 0.50  # just above OB high
        else:
            swing_highs = find_swing_highs(candles[-30:])
            sl_level = swing_highs[-1][1] + 0.50 if swing_highs else curr + 8.0
        return max(sl_level - curr, MIN_SL_DOLLARS)

    return 8.0

# ============================================
# CALCULATE LOTS
# ============================================
def calculate_lots(sl_dollars):
    if sl_dollars <= 0:
        return 0.01, 0, False
    raw_lots = (RISK_AMOUNT / sl_dollars) * 0.01
    lots     = round(raw_lots, 2)
    if lots < 0.01:
        return 0.01, round(sl_dollars * 1.0, 2), False
    lots        = min(lots, 0.50)
    actual_risk = sl_dollars * (lots / 0.01) * USD_PER_DOLLAR_MOVE_PER_001_LOT
    is_safe     = actual_risk <= RISK_AMOUNT * 2.0
    return lots, round(actual_risk, 2), is_safe

# ============================================
# MAIN SIGNAL ENGINE — FULL SMC STRATEGY
# ============================================
def generate_signal(candles_5m, candles_15m):
    if not candles_5m or not candles_15m:
        return None

    curr_price = candles_5m[-1]["close"]

    # --- 15M HIGHER TIMEFRAME BIAS (non-negotiable gate) ---
    tf_bias, bos_type, choch = analyze_structure_bos(candles_15m)

    # GATE: if 15M is NEUTRAL, no trade — no higher-TF confirmation
    if tf_bias == "NEUTRAL":
        print(f"Price: {curr_price:.2f} | 15M = NEUTRAL — no trade")
        return None

    # --- 5M ANALYSIS ---
    m5_bias, m5_bos, m5_choch = analyze_structure_bos(candles_5m)
    ob_high, ob_low, price_in_ob = detect_order_block(candles_5m, tf_bias)
    eq_highs, eq_lows, swept_high, swept_low = detect_liquidity_zones(candles_5m)
    liquidity, liq_score = detect_liquidity_grab(candles_5m)
    fvg, fvg_score       = detect_fvg(candles_5m)
    pattern, pat_score   = check_candle_pattern(candles_5m)
    rsi                  = calculate_rsi(candles_5m)

    print(f"\n--- ANALYSIS ---")
    print(f"Price:       {curr_price:.2f}")
    print(f"15M Bias:    {tf_bias} | BOS: {bos_type} | CHoCH: {choch}")
    print(f"M5 Bias:     {m5_bias} | BOS: {m5_bos}")
    print(f"Order Block: high={ob_high} low={ob_low} in_ob={price_in_ob}")
    print(f"Eq Highs:    {eq_highs} swept={swept_high}")
    print(f"Eq Lows:     {eq_lows}  swept={swept_low}")
    print(f"Liquidity:   {liquidity}")
    print(f"FVG:         {fvg}")
    print(f"Pattern:     {pattern}")
    print(f"RSI:         {rsi}")

    # --- SCORING ---
    long_score,  long_reasons  = 0, []
    short_score, short_reasons = 0, []

    # 15M structure (highest weight — non-negotiable direction)
    if tf_bias == "BULLISH":
        long_score += 30
        long_reasons.append(f"15M Bullish ({bos_type or 'trend'}{'+ CHoCH' if choch else ''})")
    elif tf_bias == "BEARISH":
        short_score += 30
        short_reasons.append(f"15M Bearish ({bos_type or 'trend'}{'+ CHoCH' if choch else ''})")

    # CHoCH bonus (reversal signal = higher conviction)
    if choch and tf_bias == "BULLISH":
        long_score += 10
        long_reasons.append("15M CHoCH Reversal")
    elif choch and tf_bias == "BEARISH":
        short_score += 10
        short_reasons.append("15M CHoCH Reversal")

    # 5M structure alignment
    if m5_bias == "BULLISH":
        long_score += 15
        long_reasons.append(f"M5 Bullish{' BOS' if m5_bos else ''}")
    elif m5_bias == "BEARISH":
        short_score += 15
        short_reasons.append(f"M5 Bearish{' BOS' if m5_bos else ''}")

    # Order block
    if price_in_ob and tf_bias == "BULLISH":
        long_score += 20
        long_reasons.append("Price in Bullish Order Block")
    elif price_in_ob and tf_bias == "BEARISH":
        short_score += 20
        short_reasons.append("Price in Bearish Order Block")

    # Liquidity sweep (stop hunt then reversal)
    if swept_low and tf_bias == "BULLISH":
        long_score += 15
        long_reasons.append("Equal Lows Swept (Buy-side liquidity taken)")
    if swept_high and tf_bias == "BEARISH":
        short_score += 15
        short_reasons.append("Equal Highs Swept (Sell-side liquidity taken)")

    # Liquidity grab (wick-based)
    if liquidity == "BULLISH_GRAB":
        long_score += 10
        long_reasons.append("Bullish Liquidity Grab")
    elif liquidity == "BEARISH_GRAB":
        short_score += 10
        short_reasons.append("Bearish Liquidity Grab")

    # FVG
    if fvg == "BULLISH_FVG":
        long_score += 10
        long_reasons.append(f"Bullish FVG (≥${MIN_FVG_SIZE})")
    elif fvg == "BEARISH_FVG":
        short_score += 10
        short_reasons.append(f"Bearish FVG (≥${MIN_FVG_SIZE})")

    # Candlestick pattern
    if pattern in ["BULLISH_ENGULF", "HAMMER"]:
        long_score += pat_score * 5
        long_reasons.append(f"Pattern: {pattern}")
    elif pattern in ["BEARISH_ENGULF", "SHOOTING_STAR"]:
        short_score += pat_score * 5
        short_reasons.append(f"Pattern: {pattern}")

    # RSI
    if rsi < 35:
        long_score += 8
        long_reasons.append(f"RSI Oversold ({rsi})")
    elif rsi > 65:
        short_score += 8
        short_reasons.append(f"RSI Overbought ({rsi})")

    print(f"Long: {long_score} | Short: {short_score} | Need: {MIN_CONFIDENCE}+")

    # --- DETERMINE SIGNAL ---
    signal, score, reasons = None, 0, []

    if long_score > short_score and long_score >= MIN_CONFIDENCE and tf_bias == "BULLISH":
        signal  = "LONG"
        score   = min(long_score, 99)
        reasons = long_reasons
    elif short_score > long_score and short_score >= MIN_CONFIDENCE and tf_bias == "BEARISH":
        signal  = "SHORT"
        score   = min(short_score, 99)
        reasons = short_reasons

    if not signal:
        print("No signal.")
        return None

    # Smart SL placement
    sl_dollars = calculate_sl_distance(
        candles_5m,
        "LONG" if signal == "LONG" else "SHORT",
        ob_high, ob_low
    )
    sl_dollars = min(sl_dollars, MAX_SL_DOLLARS)
    tp_dollars = sl_dollars * 2

    lots, actual_risk, is_safe = calculate_lots(sl_dollars)

    if not is_safe:
        print(f"Signal rejected: risk ${actual_risk:.2f} too high.")
        return None

    return {
        "signal":        signal,
        "price":         curr_price,
        "sl_dollars":    round(sl_dollars, 2),
        "tp_dollars":    round(tp_dollars, 2),
        "lots":          lots,
        "potential_loss": actual_risk,
        "confidence":    score,
        "reasons":       reasons,
        "rsi":           rsi,
        "tf_bias":       tf_bias,
        "bos_type":      bos_type or "None",
        "choch":         choch,
        "ob_high":       ob_high,
        "ob_low":        ob_low,
        "price_in_ob":   price_in_ob,
        "eq_highs":      eq_highs,
        "eq_lows":       eq_lows,
        "swept_high":    swept_high,
        "swept_low":     swept_low,
        "liquidity":     liquidity or "None",
        "fvg":           fvg or "None",
        "pattern":       pattern or "None"
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
    mode_banner  = (
        "🧪 <b>PAPER SIGNAL — NOT EXECUTED</b>\n<i>Review only.</i>\n\n"
        if PAPER_MODE else "⚡ <b>LIVE SIGNAL</b>\n\n"
    )

    ob_text = (
        f"📦 <b>Order Block:</b> {sig['ob_low']:.2f} - {sig['ob_high']:.2f} "
        f"{'✅ Price inside OB' if sig['price_in_ob'] else ''}\n"
        if sig["ob_high"] else ""
    )
    liq_text = ""
    if sig["swept_high"]:
        liq_text = "💧 <b>Liquidity:</b> Equal Highs swept (stop hunt confirmed)\n"
    elif sig["swept_low"]:
        liq_text = "💧 <b>Liquidity:</b> Equal Lows swept (stop hunt confirmed)\n"

    msg = f"""{mode_banner}⚔️ <b>XAUUSD SIGNAL</b> {emoji}
━━━━━━━━━━━━━━━━━━━━
📊 <b>Direction:</b> {direction}
💰 <b>Entry:</b> {sig["price"]:.2f} (Market)
🛑 <b>Stop Loss:</b> {sl_price:.2f} (${sig["sl_dollars"]:.2f} away)
🎯 <b>Take Profit:</b> {tp_price:.2f} (${sig["tp_dollars"]:.2f} away)
📦 <b>Lots:</b> {sig["lots"]}
⚖️ <b>R:R:</b> 1:2
🎯 <b>Confidence:</b> {sig["confidence"]}%
💵 <b>Max Risk:</b> ~${sig["potential_loss"]:.2f}
━━━━━━━━━━━━━━━━━━━━
📋 <b>WHY THIS TRADE:</b>
{reasons_text}

📈 <b>15M Bias:</b> {sig["tf_bias"]} | {sig["bos_type"]}{'+ CHoCH🔄' if sig["choch"] else ''}
{ob_text}{liq_text}📊 <b>FVG:</b> {sig["fvg"]}
🕯 <b>Pattern:</b> {sig["pattern"]}
📉 <b>RSI:</b> {sig["rsi"]}
━━━━━━━━━━━━━━━━━━━━
🕐 {now_ist}
⚠️ <i>Max {MAX_TRADES_PER_DAY} signals/day. Verify spread before acting.</i>"""

    send_telegram(msg)
    print(f"✅ Signal: {sig['signal']} @ {sig['price']} | "
          f"Conf: {sig['confidence']}% | Risk: ${sig['potential_loss']:.2f}")

# ============================================
# SESSION LABEL
# ============================================
def get_session():
    h = datetime.now(IST).hour
    if 5  <= h < 9:  return "Asian Session 🌏"
    if 13 <= h < 18: return "London Session 🇬🇧"
    if 18 <= h < 23: return "New York Session 🗽"
    return "Off Hours 🌙"

# ============================================
# MAIN LOOP
# ============================================
def main():
    global trades_today, last_trade_date, last_signal_direction, last_signal_time

    print("🚀 XAUUSD SMC Bot Starting...")
    print(f"Capital: ${CAPITAL} | Risk/trade: ${RISK_AMOUNT:.2f} | "
          f"Min Confidence: {MIN_CONFIDENCE}%")
    print(f"Paper mode: {PAPER_MODE}")

    send_telegram(
        f"🚀 <b>XAUUSD SMC Bot LIVE</b>\n\n"
        f"{'🧪 PAPER MODE' if PAPER_MODE else '⚡ LIVE MODE'}\n\n"
        f"Strategy: BOS + CHoCH + Order Blocks\n"
        f"+ Liquidity Zones + FVG + Patterns + RSI\n\n"
        f"Capital: ${CAPITAL} | Risk/trade: ${RISK_AMOUNT:.2f}\n"
        f"Min Confidence: {MIN_CONFIDENCE}%\n"
        f"Max Signals/Day: {MAX_TRADES_PER_DAY}\n"
        f"Timeframes: 5M + 15M\n\n"
        f"Scanning every 5 minutes ⚔️"
    )

    while True:
        try:
            now   = datetime.now(IST)
            today = date.today()

            if last_trade_date != today:
                trades_today = 0
                last_trade_date = today
                last_signal_direction = None
                print(f"\n📅 New day: {today}")
                send_telegram(f"📅 <b>New Day: {today}</b>\nSignals remaining: {MAX_TRADES_PER_DAY}")

            session = get_session()
            print(f"\n[{now.strftime('%H:%M')}] {session} | "
                  f"Signals: {trades_today}/{MAX_TRADES_PER_DAY}")

            if trades_today >= MAX_TRADES_PER_DAY:
                print("Daily limit reached. Sleeping 1hr...")
                time.sleep(3600)
                continue

            if now.weekday() >= 5:
                print("Weekend. Sleeping 1hr...")
                time.sleep(3600)
                continue

            print("Fetching candles...")
            candles_5m = get_candles("5min", 100)
            time.sleep(3)
            candles_15m = get_candles("15min", 100)

            if not candles_5m or not candles_15m:
                print("Fetch failed. Retry in 2min...")
                time.sleep(120)
                continue

            sig = generate_signal(candles_5m, candles_15m)

            if sig:
                now_ts = time.time()
                if (last_signal_direction == sig["signal"] and
                        now_ts - last_signal_time < 2700):
                    print("Same direction within 45min — skipping.")
                else:
                    send_signal(sig)
                    trades_today         += 1
                    last_signal_time      = now_ts
                    last_signal_direction = sig["signal"]
                    if trades_today >= MAX_TRADES_PER_DAY:
                        send_telegram(
                            f"🔴 <b>Daily Limit Reached</b>\n"
                            f"{MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY} signals sent.\n"
                            f"Resuming tomorrow."
                        )
            else:
                print("No signal. Waiting...")

            # Clock-sync: wake exactly at next 5M candle close
            ts        = time.time()
            sleep_time = (300 - (ts % 300)) + 3
            print(f"Sleeping {int(sleep_time)}s until next candle close...")
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            send_telegram("🔴 <b>Bot stopped.</b>")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(120)

if __name__ == "__main__":
    main()
