"""Detecção de alertas e envio do email diário.

Regra de alerta (conforme spec): mudança de mais de N *pontos percentuais*
na probabilidade em ~24h. Ex.: threshold 3 → 50% ontem vs ≥53% ou ≤47% hoje.
(É pontos percentuais, não variação relativa — é o que o exemplo da spec
"50% → ≤47% ou ≥53%" define.)
"""

import os
import smtplib
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from database import Database, parse_ts
from utils import get_logger

logger = get_logger("alerts")

CATEGORY_LABELS = {
    "politics": "🏛️ POLITICS",
    "finance": "💰 FINANCE",
    "geopolitics": "🌍 GEOPOLITICS",
    "economy": "📊 ECONOMY",
    "elections": "🗳️ ELECTIONS",
}


def market_url(market: dict) -> str:
    if market.get("event_slug"):
        return f"https://polymarket.com/event/{market['event_slug']}"
    if market.get("slug"):
        return f"https://polymarket.com/market/{market['slug']}"
    return "https://polymarket.com"


def detect_alerts(db: Database, threshold_pp: float = 3.0,
                  window_hours: float = 24.0,
                  tolerance_hours: float = 3.0) -> dict:
    """Compara a última rodada com a rodada ~window_hours atrás.

    Retorna {categoria: [alertas ordenados por |delta| desc]}; cada alerta tem
    market_id, question, url, yesterday, today, delta_pp (tudo em % 0-100).
    """
    latest = db.latest_ts()
    if latest is None:
        logger.warning("no snapshots in database; nothing to compare")
        return {}

    target = parse_ts(latest) - timedelta(hours=window_hours)
    reference = db.reference_ts(target, tolerance_hours)
    if reference is None or reference == latest:
        logger.warning("no reference snapshot ~%.0fh before %s; skipping alerts "
                       "(first day of data?)", window_hours, latest)
        return {}

    current = db.prices_at(latest)
    previous = db.prices_at(reference)
    meta = {m["market_id"]: m for m in db.get_markets()}

    alerts = {}
    for market_id, price_now in current.items():
        price_before = previous.get(market_id)
        if price_before is None:
            continue
        # arredonda para matar ruído binário (0.53-0.50 -> 3.0000000000000027 pp)
        delta_pp = round((price_now - price_before) * 100.0, 6)
        if abs(delta_pp) <= threshold_pp:
            continue
        market = meta.get(market_id, {})
        category = market.get("category", "other")
        alerts.setdefault(category, []).append({
            "market_id": market_id,
            "question": market.get("question", market_id),
            "url": market_url(market),
            "yesterday": price_before * 100.0,
            "today": price_now * 100.0,
            "delta_pp": delta_pp,
        })

    for category in alerts:
        alerts[category].sort(key=lambda a: abs(a["delta_pp"]), reverse=True)

    total = sum(len(v) for v in alerts.values())
    logger.info("alert detection: %s vs %s -> %d alerts (> %.1f pp)",
                latest, reference, total, threshold_pp)
    return alerts


def build_email_html(alerts: dict, latest_ts: str, total_markets: int,
                     dashboard_url: str = "") -> str:
    total_alerts = sum(len(v) for v in alerts.values())
    biggest = max((a["delta_pp"] for cat in alerts.values() for a in cat),
                  key=abs, default=0.0)
    date_label = latest_ts[:10] if latest_ts else ""

    parts = [
        f"<h2>📊 Polymarket Tracker — Alertas do Dia ({date_label})</h2>",
        "<h3>🎯 Resumo</h3>",
        "<ul>",
        f"<li>Total de mercados rastreados: {total_markets}</li>",
        f"<li>Alertas: {total_alerts}</li>",
        f"<li>Maior movimento: {biggest:+.1f} pp</li>",
        "</ul><hr>",
    ]

    for category, items in sorted(alerts.items()):
        label = CATEGORY_LABELS.get(category, category.upper())
        parts.append(f"<h3>{label}</h3><ul>")
        for a in items:
            parts.append(
                "<li><a href='{url}'>{question}</a><br>"
                "Ontem: {yesterday:.1f}% → Hoje: {today:.1f}% "
                "(<b>{delta_pp:+.1f} pp</b>) ⚠️</li>".format(**a))
        parts.append("</ul>")

    if dashboard_url:
        parts.append(f"<hr><p>🔍 <a href='{dashboard_url}'>Ver dashboard completo</a></p>")
    parts.append(f"<p style='color:#888'>Atualizado: {latest_ts} UTC</p>")
    return "\n".join(parts)


def send_email(subject: str, html: str) -> bool:
    """Envia via SMTP usando EMAIL_USER / EMAIL_PASSWORD / EMAIL_RECIPIENT.

    Host/porta opcionais em SMTP_HOST / SMTP_PORT (default Gmail, SSL 465).
    """
    user = os.environ.get("EMAIL_USER")
    password = os.environ.get("EMAIL_PASSWORD")
    recipient = os.environ.get("EMAIL_RECIPIENT", user)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    if not user or not password:
        logger.error("EMAIL_USER / EMAIL_PASSWORD not set; email NOT sent")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.sendmail(user, [recipient], msg.as_string())
        logger.info("alert email sent to %s", recipient)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("email send failed: %s", exc)
        return False
