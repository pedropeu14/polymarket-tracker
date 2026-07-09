"""Funções auxiliares: config, logging, HTTP com retry, parsing defensivo."""

import json
import logging
import os
import time

import requests

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "config.json")

DEFAULT_CONFIG = {
    "categories": {
        "politics": {"enabled": True},
        "finance": {"enabled": True},
        "geopolitics": {"enabled": True},
        "economy": {"enabled": True},
        "elections": {"enabled": True},
    },
    "threshold_alert": 3,
    "alert_run": "23:00",
    "alert_comparison_window_hours": 24,
    "alert_comparison_tolerance_hours": 3,
    "days_history": 90,
    "batch_size": 100,
    "max_markets_per_category": 100,
    "min_volume_usd": 0,
    "use_clob_midpoints": False,
    "dashboard_url": "",
    "email": {"enabled": True, "subject": "Polymarket Tracker - Alertas do Dia",
              "send_if_no_alerts": False},
}


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def load_config(path: str = None) -> dict:
    """Carrega config.json por cima dos defaults (merge raso por chave de topo)."""
    path = path or DEFAULT_CONFIG_PATH
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        with open(path, encoding="utf-8") as fh:
            user_config = json.load(fh)
        for key, value in user_config.items():
            config[key] = value
    except FileNotFoundError:
        get_logger("utils").warning("config.json not found at %s; using defaults", path)
    return config


def enabled_categories(config: dict) -> list:
    return [name for name, cat in config.get("categories", {}).items()
            if cat.get("enabled", False)]


def alert_hour_utc(config: dict) -> int:
    """Hora (UTC) da execução que dispara alertas, a partir de 'alert_run': 'HH:MM'."""
    raw = str(config.get("alert_run", "23:00"))
    try:
        return int(raw.split(":")[0])
    except (ValueError, IndexError):
        return 23


def http_get_json(session: requests.Session, url: str, params: dict = None,
                  retries: int = 3, timeout: int = 30, pause: float = 1.5):
    """GET com retry exponencial. Retorna o JSON decodificado ou None."""
    logger = get_logger("http")
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = pause * (2 ** attempt)
                logger.warning("429 rate-limited on %s; sleeping %.1fs", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                logger.error("GET %s failed after %d attempts: %s", url, retries, exc)
                return None
            wait = pause * (2 ** (attempt - 1))
            logger.warning("GET %s attempt %d failed (%s); retrying in %.1fs",
                           url, attempt, exc, wait)
            time.sleep(wait)
    return None


def parse_json_field(value):
    """A Gamma API devolve listas como JSON *stringificado* ('["Yes","No"]').

    Aceita lista nativa, string JSON ou None; retorna sempre uma lista.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def to_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
