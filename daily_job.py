"""Job de coleta — executado pelo GitHub Actions 4x/dia (05h/11h/17h/23h UTC).

Toda execução: coleta os mercados das categorias habilitadas e grava um
snapshot no SQLite. Apenas na execução das 23h UTC (config `alert_run`):
compara com a rodada de ~24h atrás e envia o email com deltas > threshold.

Uso:
    python daily_job.py                 # coleta; alerta só se hora == alert_run
    python daily_job.py --force-alerts  # coleta e roda alertas agora
    python daily_job.py --dry-run       # alertas no stdout, sem enviar email
"""

import argparse
import sys
from datetime import datetime, timezone

from alerts import build_email_html, detect_alerts, send_email
from database import Database
from polymarket_api import collect_all
from utils import alert_hour_utc, get_logger, load_config

logger = get_logger("daily_job")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Polymarket tracker collection job")
    parser.add_argument("--config", default=None, help="path to config.json")
    parser.add_argument("--db", default=None, help="path to sqlite db")
    parser.add_argument("--force-alerts", action="store_true",
                        help="run alert detection regardless of the hour")
    parser.add_argument("--dry-run", action="store_true",
                        help="print alerts instead of sending email")
    parser.add_argument("--skip-collect", action="store_true",
                        help="skip collection (alerts only)")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    db = Database(args.db)

    # 1. Coleta e snapshot (toda execução)
    if not args.skip_collect:
        markets = collect_all(config)
        if not markets:
            logger.error("collection returned 0 markets across all categories; "
                         "NOT storing an empty snapshot")
            return 1
        ts = db.store_snapshot(markets)
        logger.info("snapshot stored: %d markets at %s", len(markets), ts)
        pruned = db.prune(int(config.get("days_history", 90)))
        if pruned:
            logger.info("pruned %d snapshots older than %s days",
                        pruned, config.get("days_history", 90))

    # 2. Alertas (só na execução das 23h UTC, ou se forçado)
    now = datetime.now(timezone.utc)
    if not (args.force_alerts or now.hour == alert_hour_utc(config)):
        logger.info("hour %02d UTC != alert hour %02d; collection-only run done",
                    now.hour, alert_hour_utc(config))
        return 0

    alerts = detect_alerts(
        db,
        threshold_pp=float(config.get("threshold_alert", 3)),
        window_hours=float(config.get("alert_comparison_window_hours", 24)),
        tolerance_hours=float(config.get("alert_comparison_tolerance_hours", 3)),
    )
    total = sum(len(v) for v in alerts.values())

    email_cfg = config.get("email", {})
    if total == 0 and not email_cfg.get("send_if_no_alerts", False):
        logger.info("no alerts above threshold; no email sent")
        return 0

    html = build_email_html(alerts, db.latest_ts(), len(db.get_markets()),
                            config.get("dashboard_url", ""))
    if args.dry_run:
        # console Windows pode ser cp1252 — não deixar emoji derrubar o dry-run
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        print(html)
        return 0
    if not email_cfg.get("enabled", True):
        logger.info("email disabled in config; %d alerts detected but not sent", total)
        return 0

    ok = send_email(email_cfg.get("subject", "Polymarket Tracker - Alertas do Dia"),
                    html)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
