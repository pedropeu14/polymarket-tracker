# 📊 Polymarket Tracker

Rastreia mercados de previsão do Polymarket em 5 categorias (Politics, Finance,
Geopolitics, Economy, Elections), coleta preços 4x/dia via GitHub Actions,
envia um email diário com movimentos acima do threshold e publica um dashboard
Streamlit.

## Arquitetura

```
GitHub Actions (05h/11h/17h/23h UTC)
        │
        ▼
daily_job.py ── polymarket_api.py ──▶ GAMMA API  (mercados + preços)
        │                            CLOB API   (midpoints, opcional)
        ▼
database.py ──▶ data/polymarket.db  (SQLite, 90 dias, commitado pelo bot)
        │
        ├─ 23h UTC: alerts.py compara com ~24h atrás → email (SMTP)
        └─ Streamlit Cloud lê o .db do repo → app.py (dashboard)
```

- **Coleta**: toda execução salva um snapshot (mesmo timestamp para a rodada).
- **Alerta**: só a execução das 23h UTC compara com a rodada de ~24h atrás
  (tolerância de ±3h para atrasos do cron) e envia **1 email/dia**.
- **Threshold**: mudança em **pontos percentuais** da probabilidade
  (ex.: 3 → 50% ontem vs ≥53% ou ≤47% hoje dispara).

## Notas sobre as APIs (diferem da spec original)

- A Gamma API retorna um **array JSON** no topo, não `{"markets": [...]}`.
- Listas (`outcomes`, `outcomePrices`, `clobTokenIds`) vêm **stringificadas**
  (`'["Yes","No"]'`) — o parsing trata os dois formatos.
- O preço corrente já vem no próprio mercado (`outcomePrices`); a CLOB API é
  usada só opcionalmente (`use_clob_midpoints`) e opera por `token_id`
  (não existe `GET /prices?market_ids=`).
- Categoria = **tag** na Gamma. Cascata: `/tags/slug/{slug}` → 
  `/markets?tag_id=…&related_tags=true`; fallback `/events?tag_slug=…`.
  Categoria sem tag correspondente fica vazia com warning (nunca inventa).
- O preço rastreado é a probabilidade do desfecho **"Yes"** (ou o primeiro
  desfecho em mercados não binários), fração 0–1.

## Como rodar localmente

```bash
pip install -r requirements.txt
python daily_job.py                    # coleta um snapshot
python daily_job.py --force-alerts --dry-run   # testa alertas sem enviar email
streamlit run app.py                   # dashboard
python -m unittest discover -s tests -v        # testes (offline, sem rede)
```

⚠️ Redes corporativas costumam bloquear `*.polymarket.com` — se a coleta local
der timeout, teste em outra rede; o GitHub Actions não tem esse bloqueio.

## Deploy

1. Crie um repositório no GitHub e faça push desta pasta.
2. Em *Settings → Secrets and variables → Actions*, crie:
   - `EMAIL_USER` — seu email (Gmail: use uma [senha de app](https://myaccount.google.com/apppasswords))
   - `EMAIL_PASSWORD` — a senha de app
   - `EMAIL_RECIPIENT` — destinatário (opcional; default = EMAIL_USER)
3. O workflow `.github/workflows/daily_tracker.yml` roda 4x/dia e commita o
   `data/polymarket.db` atualizado (workflow_dispatch permite rodar na mão,
   com opção de forçar alertas).
4. Streamlit Cloud: *New app* → aponte para `app.py` deste repo. Preencha
   `dashboard_url` no `data/config.json` para o link aparecer no email.

## Configuração (`data/config.json`)

| Chave | Efeito |
|---|---|
| `categories.<nome>.enabled` | liga/desliga a categoria sem mexer em código |
| `threshold_alert` | pontos percentuais de mudança em 24h para alertar |
| `alert_run` | hora UTC da execução que compara e envia email |
| `max_markets_per_category` | corte por volume (top N) |
| `min_volume_usd` | ignora mercados ilíquidos |
| `use_clob_midpoints` | refina preços com o midpoint do order book |
| `days_history` | retenção do histórico no SQLite |
| `email.send_if_no_alerts` | enviar email mesmo sem alertas |
| `dashboard_url` | link do Streamlit Cloud no rodapé do email |

## Estrutura

```
polymarket-tracker/
├── app.py                # Dashboard Streamlit
├── polymarket_api.py     # GAMMA + CLOB (parsing defensivo)
├── database.py           # SQLite: markets + snapshots
├── alerts.py             # Detecção de deltas + email HTML
├── daily_job.py          # Orquestração (coleta sempre; alerta às 23h UTC)
├── utils.py              # Config, logging, HTTP retry, parsing
├── tests/test_tracker.py # Testes offline com fixtures da Gamma
├── data/config.json      # Configuração dinâmica
└── .github/workflows/daily_tracker.yml
```
