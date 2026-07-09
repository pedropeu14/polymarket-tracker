"""Persistência em SQLite: metadados de mercados + snapshots de preço.

Todos os timestamps são ISO-8601 em UTC ("YYYY-MM-DDTHH:MM:SSZ") — uma
execução de coleta grava todos os snapshots com o MESMO timestamp (o da
rodada), o que permite comparar rodadas entre si.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "polymarket.db")

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    market_id  TEXT PRIMARY KEY,
    question   TEXT NOT NULL,
    category   TEXT NOT NULL,
    slug       TEXT,
    event_slug TEXT,
    outcome    TEXT,
    token_id   TEXT,
    end_date   TEXT,
    first_seen TEXT,
    last_seen  TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    market_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    price     REAL NOT NULL,
    volume    REAL,
    PRIMARY KEY (market_id, ts)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots (ts);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc)


class Database:
    def __init__(self, path: str = None):
        self.path = path or DEFAULT_DB_PATH
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------- escrita

    def store_snapshot(self, markets: list, ts: str = None) -> str:
        """Grava uma rodada de coleta: upsert dos metadados + insert dos preços."""
        ts = ts or utcnow_iso()
        with self.conn:
            for m in markets:
                self.conn.execute(
                    """INSERT INTO markets (market_id, question, category, slug,
                           event_slug, outcome, token_id, end_date,
                           first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(market_id) DO UPDATE SET
                           question=excluded.question, category=excluded.category,
                           slug=excluded.slug, event_slug=excluded.event_slug,
                           outcome=excluded.outcome, token_id=excluded.token_id,
                           end_date=excluded.end_date, last_seen=excluded.last_seen""",
                    (m["market_id"], m["question"], m["category"], m.get("slug"),
                     m.get("event_slug"), m.get("outcome"), m.get("token_id"),
                     m.get("end_date"), ts, ts))
                self.conn.execute(
                    """INSERT OR REPLACE INTO snapshots (market_id, ts, price, volume)
                       VALUES (?,?,?,?)""",
                    (m["market_id"], ts, m["price"], m.get("volume")))
        return ts

    def prune(self, days: int = 90) -> int:
        """Remove snapshots mais antigos que `days` e mercados órfãos."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(TS_FORMAT)
        with self.conn:
            cur = self.conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
            self.conn.execute(
                """DELETE FROM markets WHERE market_id NOT IN
                   (SELECT DISTINCT market_id FROM snapshots)""")
        return cur.rowcount

    # ------------------------------------------------------------- leitura

    def latest_ts(self):
        row = self.conn.execute("SELECT MAX(ts) AS ts FROM snapshots").fetchone()
        return row["ts"] if row and row["ts"] else None

    def prices_at(self, ts: str) -> dict:
        """{market_id: price} de uma rodada exata."""
        rows = self.conn.execute(
            "SELECT market_id, price FROM snapshots WHERE ts = ?", (ts,))
        return {r["market_id"]: r["price"] for r in rows}

    def reference_ts(self, target: datetime, tolerance_hours: float = 3.0):
        """Timestamp de rodada mais próximo de `target`, dentro da tolerância.

        Usado para achar a rodada "de ~24h atrás" sem exigir horário exato
        (o cron do GitHub Actions atrasa alguns minutos com frequência).
        """
        lo = (target - timedelta(hours=tolerance_hours)).strftime(TS_FORMAT)
        hi = (target + timedelta(hours=tolerance_hours)).strftime(TS_FORMAT)
        rows = self.conn.execute(
            "SELECT DISTINCT ts FROM snapshots WHERE ts BETWEEN ? AND ?",
            (lo, hi)).fetchall()
        if not rows:
            return None
        return min((r["ts"] for r in rows),
                   key=lambda ts: abs((parse_ts(ts) - target).total_seconds()))

    def get_markets(self, categories: list = None) -> list:
        """Metadados dos mercados vistos na última rodada de coleta."""
        latest = self.latest_ts()
        if latest is None:
            return []
        sql = """SELECT m.* FROM markets m
                 JOIN snapshots s ON s.market_id = m.market_id AND s.ts = ?"""
        params = [latest]
        if categories:
            sql += " WHERE m.category IN (%s)" % ",".join("?" * len(categories))
            params.extend(categories)
        return [dict(r) for r in self.conn.execute(sql, params)]

    def get_history(self, market_id: str, days: int = 90) -> list:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(TS_FORMAT)
        rows = self.conn.execute(
            """SELECT ts, price FROM snapshots
               WHERE market_id = ? AND ts >= ? ORDER BY ts""",
            (market_id, cutoff))
        return [(r["ts"], r["price"]) for r in rows]

    def get_all_latest(self) -> list:
        """Última rodada completa: metadados + preço, para o dashboard/screener."""
        latest = self.latest_ts()
        if latest is None:
            return []
        rows = self.conn.execute(
            """SELECT m.*, s.price, s.volume AS volume, s.ts
               FROM snapshots s JOIN markets m ON m.market_id = s.market_id
               WHERE s.ts = ?""", (latest,))
        return [dict(r) for r in rows]
