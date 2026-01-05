import os
import time
import pandas as pd
import streamlit as st
import plotly.express as px
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("❌ Missing SUPABASE_URL / SUPABASE_KEY in environment.")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Bot Dashboard", layout="wide")
st.title("Trading Bot Dashboard (v2/v3 compatible)")

# ----------------------------
# Helpers
# ----------------------------
ACTION_INTENT_MAP = {
    "ENTER_LONG": "enter_long",
    "ENTER_SHORT": "enter_short",
    "ADD": "add",
    "REDUCE": "reduce",
    "CLOSE": "flat",
    "HOLD": "hold",
    "REVERSE": "reverse",
}

def safe_to_datetime(series: pd.Series) -> pd.Series:
    """Robust ISO8601 parsing (handles fractional seconds and timezone)."""
    return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")

def fetch_bot_logs(limit: int = 200):
    """
    Bulletproof fetch:
    - Prefer ordering by created_at
    - Fallback to timestamp (older schema / schema-cache mismatch)
    """
    try:
        res = supabase.table("bot_logs").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data or []
    except Exception:
        res = supabase.table("bot_logs").select("*").order("timestamp", desc=True).limit(limit).execute()
        return res.data or []

def fetch_trade_history(limit: int = 200):
    """
    Bulletproof fetch:
    - Prefer ordering by created_at
    - Fallback to timestamp (older schema)
    """
    try:
        res = supabase.table("trade_history").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data or []
    except Exception:
        res = supabase.table("trade_history").select("*").order("timestamp", desc=True).limit(limit).execute()
        return res.data or []

def fetch_portfolio_state():
    res = supabase.table("portfolio_state").select("*").eq("id", 1).execute().data
    return res[0] if res else None

def normalize_positions(positions: dict) -> list[dict]:
    """
    Supports BOTH:
      - old schema: { "BTC/USDT:USDT_buy": {"qty":..., "entry_price":..., "leverage":...}, ... }
      - v2/v3 schema: { "BTC/USDT:USDT": {"side":"long/short","notional_usdt":...,"entry_price":...,"leverage":...}, ... }
    """
    if not positions:
        return []

    rows = []
    latest_price = st.session_state.get("latest_price", None)

    for key, pos in positions.items():
        lev = float(pos.get("leverage", 1) or 1)
        entry = float(pos.get("entry_price", 0) or 0)

        # v2/v3 style
        if "notional_usdt" in pos or "side" in pos:
            notional = float(pos.get("notional_usdt", 0) or 0)
            side = str(pos.get("side", "long")).lower()
            collateral = (notional / lev) if lev else 0.0

            pnl = 0.0
            if latest_price and entry:
                if side == "long":
                    pnl = (latest_price - entry) / entry * notional
                else:
                    pnl = (entry - latest_price) / entry * notional

            roi = (pnl / collateral) * 100.0 if collateral > 0 else 0.0

            rows.append({
                "Symbol": key,
                "Type": "🟢 LONG" if side == "long" else "🔴 SHORT",
                "Collateral": collateral,
                "Leverage": lev,
                "Notional": notional,
                "Entry": entry,
                "PnL": pnl,
                "ROI%": roi,
            })
            continue

        # old style
        qty = float(pos.get("qty", 0) or 0)  # notional in your paper model
        collateral = (qty / lev) if lev else 0.0
        side = "long" if "buy" in str(key).lower() else "short"

        pnl = 0.0
        if latest_price and entry:
            if side == "long":
                pnl = (latest_price - entry) / entry * qty
            else:
                pnl = (entry - latest_price) / entry * qty

        roi = (pnl / collateral) * 100.0 if collateral > 0 else 0.0

        rows.append({
            "Symbol": key,
            "Type": "🟢 LONG" if side == "long" else "🔴 SHORT",
            "Collateral": collateral,
            "Leverage": lev,
            "Notional": qty,
            "Entry": entry,
            "PnL": pnl,
            "ROI%": roi,
        })

    return rows

# ----------------------------
# Load data
# ----------------------------
try:
    portfolio = fetch_portfolio_state()
    if not portfolio:
        st.error("❌ portfolio_state row id=1 not found.")
        st.stop()

    positions = portfolio.get("positions") or {}
    balance = float(portfolio.get("balance_usdt", 0) or 0)

    logs = fetch_bot_logs(limit=200)
    history = fetch_trade_history(limit=200)

except Exception as e:
    st.error(f"❌ Database Connection Error: {e}")
    st.stop()

# Debug (helps identify “No bot logs found” causes instantly)
with st.sidebar:
    st.subheader("Debug")
    st.write("SUPABASE_URL:", str(SUPABASE_URL)[:60] + ("…" if len(str(SUPABASE_URL)) > 60 else ""))
    st.write("bot_logs rows fetched:", len(logs))
    if logs:
        st.write("bot_logs keys:", list(logs[0].keys()))

# Store latest market price from logs if available
latest_price = None
if logs:
    latest_price = logs[0].get("market_price", None)
st.session_state["latest_price"] = float(latest_price) if latest_price is not None else None

# ----------------------------
# Top stats
# ----------------------------
colA, colB, colC, colD = st.columns(4)

with colA:
    st.metric("Wallet Balance (USDT)", f"${balance:,.2f}")

with colB:
    st.metric("Open Positions", f"{len(positions) if positions else 0}")

with colC:
    st.metric("Latest Price", f"${st.session_state['latest_price']:,.2f}" if st.session_state["latest_price"] else "N/A")

# Unrealized PnL
unrealized_pnl = 0.0
total_margin = 0.0
if positions and st.session_state["latest_price"]:
    for row in normalize_positions(positions):
        unrealized_pnl += float(row["PnL"])
        total_margin += float(row["Collateral"])

with colD:
    st.metric("Unrealized PnL (USDT)", f"${unrealized_pnl:,.2f}")

st.divider()

# ----------------------------
# Positions table
# ----------------------------
st.subheader("Active Positions")
pos_rows = normalize_positions(positions)
if pos_rows:
    df_pos = pd.DataFrame(pos_rows)
    df_pos_display = df_pos.copy()
    df_pos_display["Collateral"] = df_pos_display["Collateral"].map(lambda x: f"${x:,.2f}")
    df_pos_display["Notional"] = df_pos_display["Notional"].map(lambda x: f"${x:,.0f}")
    df_pos_display["Entry"] = df_pos_display["Entry"].map(lambda x: f"${x:,.2f}")
    df_pos_display["PnL"] = df_pos_display["PnL"].map(lambda x: f"${x:,.2f}")
    df_pos_display["ROI%"] = df_pos_display["ROI%"].map(lambda x: f"{x:,.2f}%")
    st.dataframe(df_pos_display, use_container_width=True)
else:
    st.info("No open positions.")

st.divider()

# ----------------------------
# Bot logs chart
# ----------------------------
st.subheader("Bot Decisions (Recent)")

if logs:
    df_chart = pd.DataFrame(logs)

    # Ensure we have a usable time column
    if "created_at" not in df_chart.columns:
        df_chart["created_at"] = df_chart.get("timestamp", None)

    df_chart["created_at"] = safe_to_datetime(df_chart["created_at"])
    df_chart = df_chart.dropna(subset=["created_at"])

    # Normalize intent/action for markers
    if "intent" in df_chart.columns:
        df_chart["intent_norm"] = (
            df_chart["intent"].astype(str).str.upper().map(ACTION_INTENT_MAP).fillna(df_chart["intent"].astype(str))
        )
    elif "action" in df_chart.columns:
        df_chart["intent_norm"] = (
            df_chart["action"].astype(str).str.upper().map(ACTION_INTENT_MAP).fillna(df_chart["action"].astype(str))
        )
    else:
        df_chart["intent_norm"] = "hold"

    # Price series
    if "market_price" in df_chart.columns:
        df_chart["market_price"] = pd.to_numeric(df_chart["market_price"], errors="coerce")

    df_chart = df_chart.sort_values("created_at")

    fig = px.line(df_chart, x="created_at", y="market_price", title="Price with Decision Markers")

    # Markers (including REDUCE)
    buys = df_chart[df_chart["intent_norm"] == "enter_long"]
    shorts = df_chart[df_chart["intent_norm"] == "enter_short"]
    adds = df_chart[df_chart["intent_norm"] == "add"]
    reduces = df_chart[df_chart["intent_norm"] == "reduce"]
    exits = df_chart[df_chart["intent_norm"].isin(["flat", "reverse"])]

    if not buys.empty:
        fig.add_scatter(x=buys["created_at"], y=buys["market_price"], mode="markers", name="Enter Long")
    if not shorts.empty:
        fig.add_scatter(x=shorts["created_at"], y=shorts["market_price"], mode="markers", name="Enter Short")
    if not adds.empty:
        fig.add_scatter(x=adds["created_at"], y=adds["market_price"], mode="markers", name="Add")
    if not reduces.empty:
        fig.add_scatter(x=reduces["created_at"], y=reduces["market_price"], mode="markers", name="Reduce")
    if not exits.empty:
        fig.add_scatter(x=exits["created_at"], y=exits["market_price"], mode="markers", name="Exit/Reverse")

    st.plotly_chart(fig, use_container_width=True)

    # Recent decisions table
    st.subheader("Recent Activity")
    recent = []
    for log in logs[:25]:
        ts_raw = log.get("created_at") or log.get("timestamp") or ""
        ts = str(ts_raw)
        tshort = ts[11:19] if "T" in ts else ts
        intent = log.get("intent") or log.get("action") or ""
        thesis = log.get("rationale") or log.get("thesis") or ""
        recent.append({"Time": tshort, "Decision": intent, "Thesis": str(thesis)[:160]})
    st.dataframe(pd.DataFrame(recent), use_container_width=True)

else:
    st.info("No bot logs found.")

st.divider()

# ----------------------------
# Trade history
# ----------------------------
st.subheader("Closed Trade History")

if history:
    # Normalize timestamps into a single display column
    for trade in history:
        ts_raw = trade.get("created_at") or trade.get("timestamp") or ""
        trade["_ts"] = ts_raw

    df_hist = pd.DataFrame(history)
    df_hist["_ts"] = safe_to_datetime(df_hist["_ts"])
    df_hist = df_hist.sort_values("_ts", ascending=False)

    show_cols = []
    for c in ["_ts", "symbol", "side", "entry_price", "exit_price", "qty", "leverage", "pnl", "fees", "roi_pct", "reason"]:
        if c in df_hist.columns:
            show_cols.append(c)

    df_show = df_hist[show_cols].copy()
    if "_ts" in df_show.columns:
        df_show["_ts"] = df_show["_ts"].dt.strftime("%Y-%m-%d %H:%M:%S")

    st.dataframe(df_show, use_container_width=True)
else:
    st.info("No closed trades yet.")

# ----------------------------
# Controls / refresh
# ----------------------------
with st.sidebar:
    st.subheader("Controls")
    auto = st.checkbox("Auto-refresh", value=False)
    interval = st.slider("Refresh interval (seconds)", 5, 120, 15)

if auto:
    time.sleep(interval)
    st.rerun()
