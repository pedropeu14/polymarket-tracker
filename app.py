"""Dashboard Streamlit — lê data/polymarket.db (atualizado pelo GitHub Actions)."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from database import DEFAULT_DB_PATH, Database, parse_ts
from utils import load_config

st.set_page_config(page_title="Polymarket Tracker", page_icon="📊", layout="wide")

CATEGORY_LABELS = {
    "politics": "🏛️ Politics",
    "finance": "💰 Finance",
    "geopolitics": "🌍 Geopolitics",
    "economy": "📊 Economy",
    "elections": "🗳️ Elections",
}


@st.cache_data(ttl=3600)
def load_data():
    """Última rodada + delta 24h por mercado, como DataFrame."""
    db = Database(DEFAULT_DB_PATH)
    try:
        rows = db.get_all_latest()
        if not rows:
            return pd.DataFrame(), None

        latest = db.latest_ts()
        reference = db.reference_ts(parse_ts(latest) - timedelta(hours=24),
                                    tolerance_hours=3)
        previous = db.prices_at(reference) if reference else {}

        df = pd.DataFrame(rows)
        df["price_pct"] = df["price"] * 100
        df["prev_pct"] = df["market_id"].map(
            lambda m: previous.get(m, float("nan"))) * 100
        df["delta_pp"] = df["price_pct"] - df["prev_pct"]
        return df, latest
    finally:
        db.close()


@st.cache_data(ttl=3600)
def load_history(market_id: str, days: int):
    db = Database(DEFAULT_DB_PATH)
    try:
        return db.get_history(market_id, days)
    finally:
        db.close()


config = load_config()
threshold = float(config.get("threshold_alert", 3))
df, latest_ts = load_data()

st.title("📊 Polymarket Tracker")

if df.empty:
    st.warning("Sem dados ainda — rode `python daily_job.py` ou aguarde a "
               "primeira coleta do GitHub Actions.")
    st.stop()

age_hours = (datetime.now(timezone.utc) - parse_ts(latest_ts)).total_seconds() / 3600
st.caption(f"Última coleta: **{latest_ts} UTC** ({age_hours:.1f}h atrás) · "
           f"threshold de alerta: {threshold:.0f} pp / 24h")

# ---------------------------------------------------------------- filtros
categories = sorted(df["category"].unique())
selected = st.multiselect(
    "Categorias",
    categories,
    default=categories,
    format_func=lambda c: CATEGORY_LABELS.get(c, c.title()),
)
search = st.text_input("Buscar mercado", "")

view = df[df["category"].isin(selected)]
if search:
    view = view[view["question"].str.contains(search, case=False, na=False)]

# ---------------------------------------------------------------- resumo
n_alerts = int((view["delta_pp"].abs() > threshold).sum())
col1, col2, col3 = st.columns(3)
col1.metric("Mercados", len(view))
col2.metric(f"Alertas (> {threshold:.0f} pp)", n_alerts)
biggest = view["delta_pp"].abs().max()
col3.metric("Maior movimento 24h", f"{biggest:+.1f} pp" if pd.notna(biggest) else "—")

# ------------------------------------------------------------ por categoria
for category in selected:
    cat_df = view[view["category"] == category].sort_values(
        "delta_pp", key=lambda s: s.abs(), ascending=False)
    if cat_df.empty:
        continue
    st.subheader(f"{CATEGORY_LABELS.get(category, category.title())} "
                 f"({len(cat_df)} mercados)")

    table = cat_df[["question", "price_pct", "delta_pp", "volume"]].copy()
    table.columns = ["Mercado", "Prob. (%)", "Δ 24h (pp)", "Volume (USD)"]
    st.dataframe(
        table.style.format({"Prob. (%)": "{:.1f}", "Δ 24h (pp)": "{:+.1f}",
                            "Volume (USD)": "{:,.0f}"}, na_rep="—")
        .map(lambda v: "color: #ff4b4b" if isinstance(v, float) and abs(v) > threshold
             else "", subset=["Δ 24h (pp)"]),
        use_container_width=True, hide_index=True,
    )

# ---------------------------------------------------------------- histórico
st.subheader("📈 Histórico de um mercado")
options = view.sort_values("volume", ascending=False)
if not options.empty:
    choice = st.selectbox(
        "Mercado", options["market_id"],
        format_func=lambda m: options.set_index("market_id").loc[m, "question"])
    days = st.slider("Janela (dias)", 7, int(config.get("days_history", 90)), 30)
    history = load_history(choice, days)
    if len(history) > 1:
        hist_df = pd.DataFrame(history, columns=["ts", "price"])
        hist_df["ts"] = pd.to_datetime(hist_df["ts"])
        hist_df["price_pct"] = hist_df["price"] * 100
        fig = px.line(hist_df, x="ts", y="price_pct",
                      labels={"ts": "", "price_pct": "Probabilidade (%)"})
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Histórico insuficiente para este mercado (mínimo 2 coletas).")
