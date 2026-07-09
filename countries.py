"""Classificação de mercados por país, a partir do texto da pergunta.

O Polymarket não expõe um campo "país" — a detecção é por palavras-chave
(país, gentílico, líderes, instituições, cidades). Heurística de filtro,
não verdade absoluta: perguntas que citam 2+ países caem no primeiro da
lista de prioridade (conflitos/tópicos específicos vêm antes das potências,
p.ex. "US strikes Iran" classifica como Irã). Sem match → Global.
"""

import re

OTHER_LABEL = "🌐 Global / Outros"

# (rótulo, tokens case-sensitive, tokens case-insensitive)
# Ordem = prioridade: tópicos/conflitos específicos antes das potências.
COUNTRIES = [
    ("🇺🇦 Ucrânia", [], ["ukraine", "ukrainian", "zelensky", "zelenskyy",
                          "kyiv", "crimea", "donbas"]),
    ("🇮🇱 Israel / Palestina", [], ["israel", "israeli", "netanyahu", "gaza",
                                     "hamas", "hezbollah", "west bank",
                                     "palestin"]),
    ("🇮🇷 Irã", [], ["iran", "iranian", "khamenei", "tehran", "hormuz"]),
    ("🇹🇼 Taiwan", [], ["taiwan"]),
    ("🇰🇵 Coreia do Norte", [], ["north korea", "kim jong"]),
    ("🇻🇪 Venezuela", [], ["venezuela", "maduro"]),
    ("🇬🇱 Groenlândia", [], ["greenland"]),
    ("🇨🇺 Cuba", [], ["cuba", "cuban"]),
    ("🇪🇬 Egito", [], ["egypt", "egyptian", "suez"]),
    ("🇪🇹 Etiópia", [], ["ethiopia", "abiy ahmed"]),
    ("🇷🇺 Rússia", [], ["russia", "russian", "putin", "moscow", "kremlin"]),
    ("🇨🇳 China", [], ["china", "chinese", "xi jinping", "beijing", "yuan"]),
    ("🇧🇷 Brasil", [], ["brazil", "brasil", "lula", "bolsonaro"]),
    ("🇦🇷 Argentina", [], ["argentina", "milei"]),
    ("🇲🇽 México", [], ["mexico", "sheinbaum"]),
    ("🇨🇦 Canadá", [], ["canada", "canadian", "carney"]),
    ("🇬🇧 Reino Unido", ["UK"], ["united kingdom", "britain", "british",
                                  "starmer", "farage", "bank of england",
                                  "london"]),
    ("🇫🇷 França", [], ["france", "french", "macron", "le pen", "paris"]),
    ("🇩🇪 Alemanha", [], ["germany", "german", "merz", "scholz", "berlin"]),
    ("🇮🇹 Itália", [], ["italy", "italian", "meloni"]),
    ("🇪🇸 Espanha", [], ["spain", "spanish"]),
    ("🇵🇱 Polônia", [], ["poland", "polish"]),
    ("🇳🇱 Holanda", [], ["netherlands", "dutch"]),
    ("🇹🇷 Turquia", [], ["turkey", "türkiye", "erdogan", "erdoğan"]),
    ("🇸🇦 Arábia Saudita", [], ["saudi"]),
    ("🇨🇭 Suíça", [], ["switzerland", "swiss"]),
    ("🇯🇵 Japão", ["BOJ"], ["japan", "japanese", "tokyo", "yen"]),
    ("🇰🇷 Coreia do Sul", [], ["south korea", "seoul"]),
    ("🇮🇳 Índia", [], ["india", "indian", "modi"]),
    ("🇪🇺 União Europeia", ["EU", "ECB"], ["european union", "eurozone",
                                            "euro area", "brussels"]),
    ("🇺🇸 Estados Unidos", ["US", "USA", "NYC", "GOP", "SEC", "CPI", "NBA",
                             "NFL", "Fed", "SCOTUS", "NYSE"],
     ["united states", "america", "u.s.", "trump", "vance", "biden", "harris",
      "newsom", "powell", "federal reserve", "federal funds", "congress",
      "senate", "white house", "supreme court", "presidential nomination",
      "democratic presidential", "republican presidential", "democrats",
      "republicans", "new york", "california", "texas", "washington",
      "inflation"]),
    ("🪙 Cripto (global)", ["BTC", "ETH", "XRP", "SOL"],
     ["bitcoin", "ethereum", "crypto", "solana", "dogecoin", "stablecoin",
      "binance", "coinbase", "microstrategy"]),
]


def _token_pattern(token: str) -> str:
    escaped = re.escape(token)
    prefix = r"\b" if token[0].isalnum() else ""
    suffix = r"\b" if token[-1].isalnum() else ""
    return prefix + escaped + suffix


_COMPILED = []
for _label, _cs, _ci in COUNTRIES:
    _pats = []
    if _cs:
        _pats.append(re.compile("|".join(_token_pattern(t) for t in _cs)))
    if _ci:
        _pats.append(re.compile("|".join(_token_pattern(t) for t in _ci), re.I))
    _COMPILED.append((_label, _pats))


def detect_country(text: str) -> str:
    """Rótulo do país mais relevante da pergunta, ou Global se nada casar."""
    if not text:
        return OTHER_LABEL
    for label, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            return label
    return OTHER_LABEL
