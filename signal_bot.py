import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API = "https://api.kraken.com/0/public/OHLC"
ASSETS = {
    "XBTUSD": "بیت‌کوین",
    "ETHUSD": "اتریوم",
    "SOLUSD": "سولانا",
}

def get_candles(pair, interval):
    response = requests.get(
        API, params={"pair": pair, "interval": interval}, timeout=25
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"خطای بازار: {data['error']}")

    result = data["result"]
    key = next(k for k in result if k != "last")
    df = pd.DataFrame(
        result[key],
        columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    # آخرین کندل ممکن است هنوز کامل نشده باشد.
    if len(df) > 1:
        df = df.iloc[:-1].reset_index(drop=True)
    if len(df) < 35:
        raise RuntimeError("داده کندل کافی دریافت نشد")
    return df

def indicators(df):
    df = df.copy()
    close = df["close"]
    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)

    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    df["macd"] = macd
    df["macd_signal"] = macd.ewm(span=9, adjust=False).mean()

    previous = close.shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous).abs(),
            (df["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = true_range.rolling(14).mean()
    df["vol_avg"] = df["volume"].rolling(20).mean()
    return df

def score(pair, interval):
    df = indicators(get_candles(pair, interval))
    last = df.iloc[-1]
    points = 1 if last["ema9"] > last["ema21"] else -1
    points += 1 if last["macd"] > last["macd_signal"] else -1

    rsi = last["rsi"]
    if pd.notna(rsi):
        if 50 < rsi < 70:
            points += 1
        elif 30 < rsi < 50:
            points -= 1
    return df, points

def analyze_coin(pair, name):
    df15, s15 = score(pair, 15)
    time.sleep(1)
    df1h, s1h = score(pair, 60)
    time.sleep(1)
    df4h, s4h = score(pair, 240)

    last = df1h.iloc[-1]
    price = float(last["close"])
    rsi = float(last["rsi"])
    atr = float(last["atr"])
    total = 0.20 * s15 + 0.35 * s1h + 0.45 * s4h

    volume_ok = (
        pd.notna(last["vol_avg"])
        and last["vol_avg"] > 0
        and last["volume"] >= 0.8 * last["vol_avg"]
    )

    long_signal = (
        total >= 1.2 and s1h >= 2 and s15 > 0 and s4h > 0
        and volume_ok and 50 <= rsi <= 68
    )
    short_signal = (
        total <= -1.2 and s1h <= -2 and s15 < 0 and s4h < 0
        and volume_ok and 30 <= rsi <= 50
    )

    lines = [
        f"📊 {name}",
        f"قیمت آخرین کندل کامل ۱ساعته: ${price:,.4f}",
        f"RSI یک‌ساعته: {rsi:.1f}",
        f"امتیاز کل: {total:.2f}",
        f"امتیازها: ۱۵دقیقه {s15} | ۱ساعت {s1h} | ۴ساعت {s4h}",
        f"تأیید حجم: {'بله' if volume_ok else 'خیر'}",
    ]

    if not np.isfinite(atr) or atr <= 0:
        lines.append("⏸️ عدم ورود: نوسان قابل محاسبه نیست.")
    elif long_signal:
        lines += [
            "🟢 سیگنال احتمالی: لانگ",
            f"قیمت بررسی: ${price:,.4f}",
            f"حد ضرر نمونه: ${price - 1.5 * atr:,.4f}",
            f"حد سود نمونه: ${price + 3 * atr:,.4f}",
            "نسبت هدف سود به ریسک: ۲ به ۱",
        ]
    elif short_signal:
        lines += [
            "🔴 سیگنال احتمالی: شورت",
            f"قیمت بررسی: ${price:,.4f}",
            f"حد ضرر نمونه: ${price + 1.5 * atr:,.4f}",
            f"حد سود نمونه: ${price - 3 * atr:,.4f}",
            "نسبت هدف سود به ریسک: ۲ به ۱",
        ]
    else:
        lines.append("⏸️ عدم ورود: شرایط کامل سیگنال برقرار نیست.")

    lines.append("⚠️ تحلیل تضمین سود نیست؛ کارمزد و لغزش قیمت لحاظ نشده. ابتدا آزمایشی بررسی کن.")
    return "\n".join(lines)

def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("متغیرهای TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID در GitHub Secrets تنظیم نشده‌اند.")

    for start in range(0, len(text), 3500):
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text[start:start + 3500]},
            timeout=25,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("تلگرام پیام را نپذیرفت.")

def main():
    report = [
        "🤖 گزارش تحلیل بازار (اجرای خودکار)",
        "زمان UTC: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    ]
    for pair, name in ASSETS.items():
        try:
            report.append(analyze_coin(pair, name))
        except Exception as exc:
            report.append(f"⚠️ تحلیل {name} انجام نشد: {str(exc)[:180]}")
        time.sleep(1)

    send_telegram("\n\n--------------------\n\n".join(report))
    print("گزارش به تلگرام ارسال شد.")

if __name__ == "__main__":
    main()
