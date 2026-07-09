"""Integração com as APIs públicas do Polymarket (sem autenticação).

- GAMMA API (https://gamma-api.polymarket.com): descoberta de mercados e
  preços correntes. IMPORTANTE: os endpoints retornam um ARRAY JSON no topo
  (não um objeto {"markets": [...]}), e campos de lista como `outcomes`,
  `outcomePrices` e `clobTokenIds` vêm como JSON *stringificado*.
- CLOB API (https://clob.polymarket.com): midpoints por `token_id` (opcional,
  refinamento — o preço primário já vem em `outcomePrices` da Gamma).

Filtro por categoria: a Gamma organiza por *tags*. Estratégia em cascata:
  1. resolve o slug da tag (GET /tags/slug/{slug}) e busca
     GET /markets?tag_id={id}&related_tags=true ...
  2. fallback: GET /events?tag_slug={slug} e achata event["markets"].
Se nenhum caminho retornar nada, a categoria fica vazia (com warning) —
nunca inventa mercados.
"""

import time

import requests

from utils import get_logger, http_get_json, parse_json_field, to_float

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

PAGE_SIZE = 100          # máximo aceito pela Gamma por página
REQUEST_PAUSE = 0.7      # pausa entre requests (rate limit ~100/min)

logger = get_logger("polymarket_api")


class PolymarketClient:
    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "polymarket-tracker/1.0 (personal research tool)",
        })
        self._tag_cache = {}

    # ------------------------------------------------------------------ tags

    def resolve_tag_id(self, slug: str):
        """slug da tag -> id numérico, via GET /tags/slug/{slug}. None se não existir."""
        if slug in self._tag_cache:
            return self._tag_cache[slug]
        data = http_get_json(self.session, f"{GAMMA_API}/tags/slug/{slug}")
        tag_id = None
        if isinstance(data, dict):
            tag_id = data.get("id")
        elif isinstance(data, list) and data:
            tag_id = data[0].get("id")
        self._tag_cache[slug] = tag_id
        if tag_id is None:
            logger.warning("tag slug '%s' not found on Gamma API", slug)
        return tag_id

    # -------------------------------------------------------------- mercados

    def get_markets_by_category(self, category: str, max_markets: int = 100,
                                min_volume: float = 0) -> list:
        """Mercados ativos de uma categoria, normalizados e ordenados por volume."""
        markets = self._markets_via_tag_id(category, max_markets)
        if not markets:
            logger.info("category '%s': tag_id path empty, trying events fallback",
                        category)
            markets = self._markets_via_events(category, max_markets)

        normalized = []
        for raw in markets:
            market = self._normalize_market(raw, category)
            if market is None:
                continue
            if min_volume and (market["volume"] or 0) < min_volume:
                continue
            normalized.append(market)

        # dedup por id (um mercado pode aparecer em mais de um evento)
        seen, unique = set(), []
        for market in normalized:
            if market["market_id"] not in seen:
                seen.add(market["market_id"])
                unique.append(market)

        unique.sort(key=lambda m: m["volume"] or 0, reverse=True)
        unique = unique[:max_markets]
        logger.info("category '%s': %d markets collected", category, len(unique))
        return unique

    def _markets_via_tag_id(self, category: str, max_markets: int) -> list:
        tag_id = self.resolve_tag_id(category)
        if tag_id is None:
            return []
        collected, offset = [], 0
        while len(collected) < max_markets:
            params = {
                "tag_id": tag_id, "related_tags": "true",
                "active": "true", "closed": "false", "archived": "false",
                "limit": PAGE_SIZE, "offset": offset,
                "order": "volumeNum", "ascending": "false",
            }
            page = http_get_json(self.session, f"{GAMMA_API}/markets", params)
            page = _unwrap_list(page, "markets")
            if not page:
                break
            collected.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(REQUEST_PAUSE)
        return collected

    def _markets_via_events(self, category: str, max_markets: int) -> list:
        collected, offset = [], 0
        while len(collected) < max_markets:
            params = {
                "tag_slug": category,
                "active": "true", "closed": "false", "archived": "false",
                "limit": PAGE_SIZE, "offset": offset,
                "order": "volume", "ascending": "false",
            }
            page = http_get_json(self.session, f"{GAMMA_API}/events", params)
            page = _unwrap_list(page, "events")
            if not page:
                break
            for event in page:
                event_slug = event.get("slug")
                for market in event.get("markets") or []:
                    market.setdefault("_event_slug", event_slug)
                    collected.append(market)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            time.sleep(REQUEST_PAUSE)
        return collected

    @staticmethod
    def _normalize_market(raw: dict, category: str):
        """Extrai os campos usados pelo tracker. Retorna None se não houver preço.

        O preço rastreado é a probabilidade do desfecho "Yes" (ou do primeiro
        desfecho, para mercados não binários), em fração 0-1.
        """
        market_id = str(raw.get("id") or raw.get("conditionId") or "").strip()
        question = (raw.get("question") or raw.get("title") or "").strip()
        if not market_id or not question:
            return None

        outcomes = [str(o) for o in parse_json_field(raw.get("outcomes"))]
        prices = parse_json_field(raw.get("outcomePrices"))
        token_ids = parse_json_field(raw.get("clobTokenIds"))

        idx = 0
        for i, outcome in enumerate(outcomes):
            if outcome.lower() == "yes":
                idx = i
                break

        price = to_float(prices[idx]) if idx < len(prices) else None
        if price is None:
            return None  # sem preço não há o que rastrear
        price = min(max(price, 0.0), 1.0)

        return {
            "market_id": market_id,
            "question": question,
            "category": category,
            "slug": raw.get("slug") or "",
            "event_slug": raw.get("_event_slug") or _first_event_slug(raw),
            "outcome": outcomes[idx] if idx < len(outcomes) else "Yes",
            "price": price,
            "token_id": str(token_ids[idx]) if idx < len(token_ids) else "",
            "volume": to_float(raw.get("volumeNum") or raw.get("volume"), 0.0),
            "end_date": raw.get("endDate") or "",
        }

    # ------------------------------------------------------------------ CLOB

    def get_clob_midpoints(self, token_ids: list, batch_size: int = 100) -> dict:
        """Midpoints do order book: POST /midpoints com [{"token_id": ...}].

        Retorna {token_id: preço}. Falha de qualquer lote é tolerada (dict
        parcial) — o chamador usa o preço da Gamma como fallback.
        """
        midpoints = {}
        token_ids = [t for t in token_ids if t]
        for start in range(0, len(token_ids), batch_size):
            batch = token_ids[start:start + batch_size]
            try:
                resp = self.session.post(
                    f"{CLOB_API}/midpoints",
                    json=[{"token_id": t} for t in batch], timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    for token, value in data.items():
                        mid = to_float(value.get("mid") if isinstance(value, dict)
                                       else value)
                        if mid is not None:
                            midpoints[token] = mid
            except (requests.RequestException, ValueError, AttributeError) as exc:
                logger.warning("CLOB midpoints batch failed (%s); using Gamma prices",
                               exc)
            time.sleep(REQUEST_PAUSE)
        return midpoints


def _unwrap_list(data, key: str) -> list:
    """Aceita tanto array puro quanto objeto {key: [...]} (defensivo)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = data.get(key) or data.get("data")
        if isinstance(inner, list):
            return inner
    return []


def _first_event_slug(raw: dict) -> str:
    events = raw.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return events[0].get("slug") or ""
    return ""


def collect_all(config: dict, client: PolymarketClient = None) -> list:
    """Coleta os mercados de todas as categorias habilitadas na config."""
    client = client or PolymarketClient()
    max_markets = int(config.get("max_markets_per_category", 100))
    min_volume = float(config.get("min_volume_usd", 0))
    all_markets = []
    for category, cat_cfg in config.get("categories", {}).items():
        if not cat_cfg.get("enabled", False):
            logger.info("category '%s' disabled; skipping", category)
            continue
        all_markets.extend(
            client.get_markets_by_category(category, max_markets, min_volume))
        time.sleep(REQUEST_PAUSE)

    if config.get("use_clob_midpoints"):
        midpoints = client.get_clob_midpoints(
            [m["token_id"] for m in all_markets],
            int(config.get("batch_size", 100)))
        for market in all_markets:
            if market["token_id"] in midpoints:
                market["price"] = midpoints[market["token_id"]]

    return all_markets
