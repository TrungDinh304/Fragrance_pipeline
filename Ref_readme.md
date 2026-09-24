# Tiki Lakehouse

A self-contained lakehouse demo for Tiki product / review analytics. The stack
crawls Tiki's public APIs, lands raw JSON as partitioned Parquet on MinIO,
shapes it with dbt + DuckDB into a silver and lakehouse-marts layer, exposes it
through Trino for ad-hoc SQL, and serves a Vietnamese-language RAG chatbot
backed by pgvector. Dashboards are **bring-your-own** — Power BI Desktop reads
the marts directly from MinIO via Parquet (or via Trino over ODBC).

---

## Architecture

```mermaid
flowchart LR
  subgraph Internet[" "]
    TikiAPI["Tiki.vn JSON API"]
  end

  subgraph Host["Host machine"]
    PBI["Power BI Desktop<br/>(manual dashboard)"]
    LLMGW["OpenAI-compatible LLM gateway<br/>(9router / OpenRouter / Ollama)"]
  end

  subgraph Docker["Docker network: lakehouse_net"]
    direction LR
    Airflow["Airflow<br/>(LocalExecutor)"]
    Crawler["crawler/<br/>fetch_tiki, fetch_category"]
    DBT["dbt run<br/>(DuckDB 1.10 + httpfs)"]
    RAGIdx["scripts/rag_index.py<br/>BGE-M3 encode"]

    subgraph Storage["Storage"]
      MinIO[("MinIO<br/>bronze · silver · lakehouse")]
      PG[("PostgreSQL 15<br/>+ pgvector")]
    end

    Trino["Trino"]
    Chatbot["Streamlit chatbot"]
  end

  TikiAPI -->|HTTP JSON| Crawler
  Airflow -.->|BashOperator| Crawler
  Airflow -.->|BashOperator| DBT
  Airflow -.->|BashOperator| RAGIdx

  Crawler -->|write Parquet| MinIO
  MinIO -->|read bronze| DBT
  DBT -->|write silver + marts| MinIO
  MinIO -->|read marts| RAGIdx
  RAGIdx -->|upsert vector(1024)| PG

  MinIO -.->|S3 / Iceberg| Trino
  Trino -->|Trino ODBC| PBI
  MinIO -->|direct Parquet<br/>via DuckDB connector| PBI

  Chatbot -->|query embed| Chatbot
  Chatbot -->|cosine top-K| PG
  Chatbot -->|chat completion| LLMGW
```

---

## Components

| Layer | Service | What it does |
| :--- | :--- | :--- |
| Ingestion | `crawler/fetch_tiki.py` + `fetch_category.py` | Watermark-rotated crawler over Tiki listing / detail / review / seller / menu-config endpoints. Writes Parquet partitioned by `dt=YYYY-MM-DD/run_id=...`. |
| Storage | MinIO (`minio/minio:latest`) | S3-compatible object store. Buckets: `bronze`, `silver`, `lakehouse`. |
| Transformation | dbt 1.11 + DuckDB 1.10 (`tiki-airflow:local` venv) | Reads bronze Parquet via httpfs, materializes external Parquet for silver + marts. |
| Marts metastore | (none) | Marts are plain Parquet files in `s3://lakehouse/marts/`. No external catalog required. |
| Query engine | Trino (`trinodb/trino:latest`) | Optional SQL serving for BI tools that prefer ODBC over direct Parquet. |
| Orchestration | Airflow 2 (`apache/airflow`, standalone) | Schedules + runs all DAGs. Metadata DB lives in the shared Postgres. |
| Vector store | PostgreSQL 15 + pgvector | Stores 1024-dim BGE-M3 embeddings in `rag.product_embeddings`. |
| Embedding model | sentence-transformers BGE-M3 (~2.3 GB, cached in `hf_cache` volume) | Multilingual, runs CPU-only inside the container. |
| RAG UI | Streamlit chatbot (`chatbot/`) | Hybrid retrieval (structured filters + pgvector cosine) → streams a chat completion from an external LLM gateway on the host. |
| Caching | Redis (`redis:latest`) | Crawler-side dedupe of seller-info responses. Not on the BI hot path. |
| BI / dashboards | **Power BI Desktop (manual)** | Connects to either Trino (ODBC) or directly to MinIO Parquet via the DuckDB connector. |

---

## Detailed dataflow — one daily run

```
┌── 00:00 UTC daily ──── DAG: tiki_lakehouse_daily_pipeline ───────────────────────┐
│                                                                                    │
│  ┌─ crawl_tiki_data ────────────────────────────────────────────────────────────┐ │
│  │                                                                               │ │
│  │  category_selector.plan_crawl(BUDGET_PER_RUN)                                │ │
│  │    ├─ read s3://lakehouse/marts/dim_categories.parquet  (leaves to pick)     │ │
│  │    ├─ read s3://bronze/_state/category_watermark.json   (last-crawled times) │ │
│  │    └─ pick N leaves with oldest last_crawled_at                              │ │
│  │                                                                               │ │
│  │  for each leaf cat in plan["chosen"]:                                        │ │
│  │    ├─ GET tiki.vn listing pages   (40 products × PAGES_PER_CATEGORY)         │ │
│  │    ├─ GET tiki.vn product detail  (one per product)                          │ │
│  │    ├─ GET tiki.vn seller widget   (Redis-cached on URL+params)               │ │
│  │    ├─ GET tiki.vn reviews         (top-rated, paginated)                     │ │
│  │    └─ write Parquet to:                                                       │ │
│  │         s3://bronze/tiki_products/dt=YYYY-MM-DD/run_id=YYYYMMDD_HHMMSS/      │ │
│  │         s3://bronze/tiki_product_details/dt=.../run_id=.../                  │ │
│  │         s3://bronze/tiki_sellers/dt=.../run_id=.../                          │ │
│  │         s3://bronze/tiki_reviews/dt=.../run_id=.../                          │ │
│  │                                                                               │ │
│  │  every TRIGGER_DBT_EVERY_N=5 cats (set via task env):                        │ │
│  │    └─ dbt run --select path:models/staging                                   │ │
│  │       → silver Parquet refreshes mid-crawl, so Trino / PBI see new data      │ │
│  │         BEFORE the whole batch finishes                                       │ │
│  │                                                                               │ │
│  │  bump category_watermark.json + write s3://bronze/_manifests/.../*.json      │ │
│  └────────────────────────────────────┬──────────────────────────────────────────┘ │
│                                       │ trigger_rule=ALL_DONE                       │
│                                       ↓                                             │
│  ┌─ run_dbt (full rebuild) ─────────────────────────────────────────────────────┐ │
│  │  dbt run --profiles-dir . (DuckDB → MinIO via httpfs)                        │ │
│  │                                                                               │ │
│  │  staging (silver layer, materialized=external parquet)                        │ │
│  │    stg_tiki_products · stg_tiki_product_details · stg_tiki_sellers           │ │
│  │    stg_tiki_seller_info · stg_tiki_reviews · stg_tiki_categories             │ │
│  │    → s3://silver/<model>.parquet                                              │ │
│  │                                                                               │ │
│  │  marts (lakehouse layer, materialized=external parquet)                       │ │
│  │    dim_products · dim_sellers · dim_categories                                │ │
│  │    fct_reviews · fct_tiki_products                                            │ │
│  │    → s3://lakehouse/marts/<model>.parquet                                     │ │
│  └──────────────────┬─────────────────────────────┬──────────────────────────────┘ │
│                     │                             │                                  │
│                     ↓                             ↓                                  │
│  ┌─ archive_processed_bronze ────────┐  ┌─ rag_index_products ─────────────────┐ │
│  │  copy + delete:                    │  │  read s3://lakehouse/marts/           │ │
│  │    bronze/<entity>/dt=*/           │  │       dim_products.parquet             │ │
│  │       → bronze/<entity>/           │  │  + top-5 reviews per product from     │ │
│  │         _processed/dt=*/           │  │       fct_reviews.parquet              │ │
│  │                                    │  │                                        │ │
│  │  next dbt run sees only fresh      │  │  build doc (~4 KB / product)           │ │
│  │  bronze partitions (except         │  │  → content_hash dedup vs existing      │ │
│  │  stg_tiki_categories, which reads  │  │     rag.product_embeddings             │ │
│  │  BOTH live and _processed/ —       │  │     → skip unchanged products          │ │
│  │  see staging model header)         │  │                                        │ │
│  └─────────────────┬──────────────────┘  │  BGE-M3 encode in batches of 4         │ │
│                    ↓                     │     (RAG_INDEX_BATCH=4,                 │ │
│  ┌─ generate_analytics_report ───────┐  │      OMP_NUM_THREADS=2 to keep the      │ │
│  │  matplotlib bar charts → PNGs:     │  │      Airflow worker heartbeating)      │ │
│  │     images/top_10_categories.png   │  │                                        │ │
│  │     images/top_10_sellers.png      │  │  upsert into Postgres                  │ │
│  │     images/top_10_products.png     │  │     rag.product_embeddings (1024-dim)  │ │
│  └────────────────────────────────────┘  └────────────────────────────────────────┘ │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

Two side-DAGs run independently:

```
06:00 UTC  tiki_dbt_refresh         dbt run (full) → analytics (no crawl)
07:00 UTC  tiki_rag_indexer         rag_index_products only — backstop / on-demand re-trigger
day 1, 00:00 UTC  tiki_category_monthly_pipeline
                                    fetch_category.py → dbt run --select stg_tiki_categories+ → archive
```

---

## Storage layout

```
s3://bronze/                                # raw landing zone
  tiki_products/dt=YYYY-MM-DD/run_id=*/page_*.parquet
  tiki_product_details/dt=*/run_id=*/details_*.parquet
  tiki_sellers/dt=*/run_id=*/sellers_*.parquet
  tiki_reviews/dt=*/run_id=*/reviews_*.parquet
  tiki_categories/dt=*/run_id=*/categories.parquet
  tiki_categories/_processed/dt=*/run_id=*/...     # archived but still read by stg
  _processed/                                       # all other entities archived after dbt
  _state/category_watermark.json                    # crawler rotation state
  _manifests/dt=*/<run_id>.json                     # success markers

s3://silver/                                # staging layer (one parquet per model)
  stg_tiki_products.parquet
  stg_tiki_categories.parquet
  ...

s3://lakehouse/marts/                       # gold layer — connect Power BI here
  dim_products.parquet
  dim_sellers.parquet
  dim_categories.parquet
  fct_reviews.parquet
  fct_tiki_products.parquet      # legacy `fct_tiki_books.parquet` may also be present from older runs (orphan, safe to delete)

postgres://metastore/rag.product_embeddings  # 1024-dim BGE-M3 vectors per product_id
```

---

## Project structure

```text
tiki-lakehouse/
├── airflow/                 # Airflow Docker build context + start.sh
├── chatbot/                 # Streamlit RAG UI
│   ├── app.py · rag.py · embeddings.py · llm.py · prompts.py
│   └── Dockerfile
├── crawler/                 # ingestion
│   ├── fetch_tiki.py        # main crawler (rotation + dbt micro-batch)
│   ├── fetch_category.py    # menu-config crawler (monthly)
│   ├── category_selector.py # watermark + leaves picker
│   ├── archive_processed.py # bronze partition lifecycle
│   └── tests/
├── dags/                    # 4 Airflow DAGs
│   ├── tiki_lakehouse_dag.py            # daily ELT + rag_index
│   ├── tiki_dbt_refresh_dag.py          # dbt-only backstop (06:00)
│   ├── tiki_rag_indexer_dag.py          # embed-only standalone (07:00)
│   └── tiki_category_dag.py             # monthly category refresh
├── dbt_tiki/                # dbt + DuckDB
│   ├── models/staging/      # silver layer (stg_*)
│   ├── models/marts/        # gold layer (dim_*, fct_*)
│   ├── macros/external_path.sql
│   └── profiles.yml
├── scripts/
│   ├── rag_index.py         # BGE-M3 embed + pgvector upsert
│   ├── analytics_plot.py    # matplotlib charts (DAG task)
│   ├── wait_for_airflow.py  # cross-platform poll for `make up`
│   └── run_project.sh
├── trino/etc/               # Trino config + catalogs
├── tools/                   # ad-hoc inspectors (MinIO, DuckDB)
├── Makefile                 # cross-shell entry points
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

---

## Prerequisites

- Docker Desktop (Mac / Windows) or Docker Engine + Compose v2 (Linux). **Allocate ≥ 6 GB RAM** to Docker — BGE-M3 + Airflow + Trino + Postgres + MinIO are tight at the 4 GB default.
- Python 3.10+ on the host (only needed for `make` helpers and local linting).
- [`uv`](https://github.com/astral-sh/uv) package manager.
- An OpenAI-compatible LLM endpoint reachable from the host (for the chatbot). Examples: a [9router](https://github.com/9-router) instance on `localhost:20128`, OpenRouter, a self-hosted Ollama with `--openai`, or the paid DeepSeek API. The chatbot reaches host services via `host.docker.internal`.

---

## Quickstart

```bash
cp .env.example .env       # then edit LLM_API_KEY, optionally bump BUDGET_PER_RUN
make up                    # bring up the stack (waits for Airflow ready)
make bootstrap             # unpause DAGs + trigger the categories DAG
# wait ~1-2 min in http://localhost:8081 for tiki_category_monthly_pipeline to finish, then:
docker compose exec airflow airflow dags trigger tiki_lakehouse_daily_pipeline
```

Service URLs after `make up`:

| Service | URL | Default creds |
| :--- | :--- | :--- |
| Airflow | http://localhost:8081 | `admin` / `admin` (from `.env`) |
| Chatbot (RAG) | http://localhost:8501 | — |
| MinIO Console | http://localhost:9001 | `admin` / `minio_password` |
| Trino | http://localhost:8080 | `admin` (no password, dev profile) |

---

## First-time bootstrap / Full reset

Use this when starting from an empty state (no volumes) or after `docker compose down -v`.

### Caveats before wiping volumes

- The `hf_cache` volume stores the **BGE-M3 model (~2.3 GB)**. Wiping it forces a re-download (~10–30 min) on the next `rag-indexer-init` or `chatbot` start. Prefer **Option B (selective wipe)** below.
- `.env` and `ds2api/`-style host bind-mounts survive `down -v` — LLM gateway URL + key are preserved.
- After `airflow_db` is wiped, Airflow recreates the admin user with a new internal id. Any stale browser cookie at `http://localhost:8081` returns HTTP 500 — **clear cookies / hard-refresh** after the Airflow container reboots.
- `BUDGET_PER_RUN=1` in `.env` only crawls **one leaf category per run** — the RAG index will be very narrow. Bump to `BUDGET_PER_RUN=20` (or higher) before the first crawl if you want broader coverage out of the box.
- Allocate ≥ 6 GB RAM to Docker Desktop. The RAG indexer's BGE-M3 + Airflow + Postgres + Trino + MinIO are tight at the 4 GB default → swap thrash → `rag_index_products` heartbeat-times-out instead of progressing.

### Option A — wipe everything

```bash
docker compose down -v
```

### Option B — wipe state, keep model cache (recommended)

```bash
docker compose down
docker volume ls | findstr tiki-elt        # PowerShell  (or | grep on bash)
docker volume rm tiki-elt_minio_data tiki-elt_pg_data tiki-elt_trino_metastore tiki-elt_airflow_db
```

### Bring up + bootstrap

```bash
make up                                                                                   # docker bring-up, waits for Airflow
make bootstrap                                                                            # unpause DAGs + trigger category DAG
docker compose exec airflow airflow dags trigger tiki_lakehouse_daily_pipeline           # after category DAG finishes
```

Both `make` targets are cross-shell (PowerShell / Git Bash / POSIX) — Make
invokes its own shell, so the loop inside `bootstrap` works even when you
invoke `make` from PowerShell.

### Verify each stage

```bash
# Marts materialized?
docker compose exec airflow ls /opt/project/dbt_tiki/target/

# Embeddings landed in pgvector?
docker compose exec postgres psql -U admin -d metastore -c "SELECT category_name, COUNT(*) FROM rag.product_embeddings GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"

# Bronze partitions in MinIO — open http://localhost:9001 → bronze bucket
```

### Filling specific category gaps

```bash
make crawl-cats IDS=8322,1882                             # comma-separated leaf ids
docker compose exec airflow airflow dags trigger tiki_rag_indexer
```

---

## DAGs at a glance

| DAG | Schedule | Tasks (in order) | Purpose |
| :--- | :--- | :--- | :--- |
| `tiki_lakehouse_daily_pipeline` | `0 0 * * *` | `crawl_tiki_data` → `run_dbt` → `archive_processed_bronze` → `generate_analytics_report` + `rag_index_products` (parallel) | Main ELT. Embeds new marts at the end so the chatbot sees fresh data without a separate run. |
| `tiki_dbt_refresh` | `0 6 * * *` | `run_dbt` → `generate_analytics_report` | Backstop. Rebuilds marts + analytics from whatever bronze exists, even if the daily crawl failed. |
| `tiki_rag_indexer` | `0 7 * * *` | `rag_index_products` | Standalone embed task. Useful as a backstop or after `make crawl-cats` to re-index without re-crawling. |
| `tiki_category_monthly_pipeline` | `0 0 1 * *` | `crawl_tiki_categories` → `run_dbt_categories` (`--select stg_tiki_categories+`) → `archive_processed_categories` | Refreshes `dim_categories`. Categories change rarely; the daily crawler picks leaves from the most recent dim. |

DAGs default to **paused** on a fresh `airflow_db` — `make bootstrap` unpauses
all four.

---

## BI consumption (Power BI)

There is **no Superset / no embedded BI service**. The marts are plain Parquet
files in `s3://lakehouse/marts/`, so any BI tool that speaks Parquet (or Trino)
can connect.

### Option A — Power BI ↔ Trino (ODBC)

```
Power BI Desktop  ──── Trino ODBC ───→  http://localhost:8080
                                          │
                                          └─ reads s3://lakehouse/marts/*.parquet  from MinIO
```

1. Install [Trino ODBC driver](https://trino.io/docs/current/client/odbc.html).
2. Power BI → *Get Data* → *ODBC* → DSN pointing at `localhost:8080`, catalog `iceberg` (or the catalog you configured in `trino/etc/catalog/`).
3. Use Trino schemas/tables matching `dim_products`, `fct_reviews`, etc.

### Option B — Power BI ↔ MinIO Parquet directly (DuckDB connector)

```
Power BI Desktop  ──── DuckDB connector ───→  MinIO (localhost:9000)
                                                  │
                                                  └─ read_parquet('s3://lakehouse/marts/dim_products.parquet')
```

1. Install the community [DuckDB ODBC driver](https://duckdb.org/docs/api/odbc/overview).
2. Connect Power BI to a DuckDB in-memory instance and run:
   ```sql
   INSTALL httpfs; LOAD httpfs;
   SET s3_endpoint='localhost:9000';
   SET s3_access_key_id='admin';
   SET s3_secret_access_key='minio_password';
   SET s3_use_ssl=false;
   SET s3_url_style='path';
   CREATE VIEW dim_products AS SELECT * FROM read_parquet('s3://lakehouse/marts/dim_products.parquet');
   ```
3. Build the model on the views.

Option B is the fastest path for a single-user dashboard — no Trino restart
loop and you get DuckDB's columnar engine for free. Option A is the safer pick
when multiple analysts share the warehouse.

---

## RAG chatbot

Hybrid retrieval over `rag.product_embeddings`:

```
User query
   │
   ├─→ embed via BGE-M3 (in-container, 1024-dim normalized)
   │       │
   │       ↓
   │   pgvector cosine top-K   ← optional structured filters
   │       │                     (price range, min rating, category ILIKE)
   │       ↓
   │   Hit[] → prompts.format_context()
   │       │      "Sản phẩm liên quan: ..."
   │       ↓
   │   ChatCompletion (stream) ─→ LLM gateway on host
   │       │                       (LLM_BASE_URL in .env)
   │       ↓
   └─← stream chunks → Streamlit UI
```

### Setup

1. Start an OpenAI-compatible gateway on the host (default expected at `http://host.docker.internal:20128/v1`). Examples: 9router, OpenRouter local proxy, Ollama with `--openai`.
2. Set `LLM_BASE_URL`, `LLM_API_KEY`, `CHAT_MODEL` in `.env`.
3. Bring everything up: `make up`. The `rag-indexer-init` one-shot service runs once on `up` and populates `rag.product_embeddings` from whatever marts exist (or exits 0 with a hint if marts aren't there yet).
4. Open http://localhost:8501.

### Network note

The chatbot container reaches the host gateway via `host.docker.internal`.
Compose sets `extra_hosts: host.docker.internal:host-gateway` so this works on
Linux (Docker Desktop sets it automatically on Mac/Windows). If you see
`Connection refused`, the gateway either isn't running or is bound to
`127.0.0.1` only — bind it to `0.0.0.0:<port>` so containers can reach it.

### Force a re-index

```bash
docker compose exec airflow airflow dags trigger tiki_rag_indexer
# or, bypassing Airflow:
docker compose run --rm rag-indexer-init
```

---

## Developer commands

| Command | Description |
| :--- | :--- |
| `make setup` | Create local `.venv` and install dependencies |
| `make up` | Bring up the full stack in dependency order (waits for Airflow) |
| `make bootstrap` | One-shot: unpause all DAGs + trigger the categories DAG (after a fresh `airflow_db`) |
| `make down` | Stop Docker services |
| `make crawl` | Run the Tiki crawler on the host (watermark-rotated; picks oldest categories) |
| `make crawl-cats IDS=...` | Crawl specific leaf category ids inside the Airflow container (bypasses rotation) |
| `make crawl-categories` | Run the category crawler on the host |
| `make dbt-run` | Run dbt models on the host |
| `make archive` | Archive processed bronze partitions on the host |
| `make lint` | Black + Flake8 + sqlfluff |
| `make test` | Pytest on the crawler |
| `make airflow-start` | `airflow standalone` on the host (legacy; prefer the containerized DAGs) |

`crawl-cats` is the on-demand path for filling specific gaps in the RAG index
without re-running the full pipeline. It runs inside the Airflow container so
the `minio:9000` hostname resolves and the project venv (boto3 / duckdb) is
already there.

---

## Troubleshooting

Known gotchas — all captured here so they don't bite you twice.

- **`rag_index_products` killed by SIGTERM after ~30 min, log frozen at "Loading weights"**. Not `execution_timeout` — it's the Airflow scheduler's heartbeat watchdog. PyTorch saturates every CPU core, the worker process can't send heartbeats, scheduler marks it zombie. Fix already wired into both DAGs: `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, `RAG_INDEX_BATCH=4`, `PYTHONUNBUFFERED=1`, `python -u`. If you still hit it, bump Docker Desktop RAM ≥ 6 GB.
- **`dim_categories.parquet` is 857 bytes / 1 row**. Two compounding bugs lived here until 2026-06: (a) `stg_tiki_categories` filtered on `menu_id IS NOT NULL` but the current Tiki menu-config API doesn't return `id`/`code`/`key` — `category_id` (extracted from the `/c<digits>` link) is the real key; (b) the staging model only globbed live bronze, but `archive_processed.py` moves files to `_processed/` right after the first dbt run, so every subsequent rebuild produced empty rows. Both fixed in `stg_tiki_categories.sql` — verify with `SELECT COUNT(*) FROM read_parquet('s3://lakehouse/marts/dim_categories.parquet')`.
- **Chatbot 401: `Invalid token. If this should be a DS2API key, add it to config.keys first.`** When you're proxying through ds2api (or any OpenAI gateway), the client token must match what the gateway expects. For ds2api specifically the check is against `api_keys[].key`, **not** the top-level `keys` array — the error message is misleading. Set `LLM_API_KEY` in `.env` to match, then `docker compose up -d --no-deps chatbot` to recreate so the env var reloads.
- **`make up` fails with `process_begin: CreateProcess(NULL, # 1. Infra ..., ...) failed`**. GNU Make on Windows fell back to `cmd.exe` and tried to execute the `#` comment in a recipe as a command. The Makefile is now cmd-compatible (no inline `#` comments in recipes, no `until`/`for d in`/`[ -z $X ]`). If you add a new recipe, follow the same rule.
- **Airflow UI returns HTTP 500 right after a volume wipe**. Stale cookie — the recreated admin user has a new internal id. Hard-refresh or clear cookies for `localhost:8081`.
- **RAG retrieval surfaces fans and irons when you ask about books**. The dataset is what the crawler has touched so far. Check `SELECT category_name, COUNT(*) FROM rag.product_embeddings GROUP BY 1` before assuming retrieval is broken; if the category isn't there, `make crawl-cats IDS=<leaf_id>` to fill it, then trigger `tiki_rag_indexer`.
- **`fct_tiki_books.parquet` still in MinIO after the books → products rename**. The dbt models `stg_tiki_books` / `fct_tiki_books` were renamed to `*_products` on 2026-06-09. dbt's external materialization writes the new files (`stg_tiki_products.parquet`, `fct_tiki_products.parquet`) without touching the old ones. Old marts are orphaned but harmless — `mc rm myminio/silver/stg_tiki_books.parquet myminio/lakehouse/marts/fct_tiki_books.parquet` to clean up. `analytics_plot.py` reads the new file with a fallback to the legacy name, so dashboards keep working through the transition.

---

## Testing and linting

```bash
uv run black crawler/
uv run flake8 crawler/
cd dbt_tiki && uv run sqlfluff lint models --dialect duckdb
uv run pytest crawler/tests/
```

---

## Goal

A hands-on lakehouse demo for e-commerce analytics, built to run locally, be
inspected with SQL, and extended with new models or sources. Visualization is
deliberately decoupled so you can plug in whichever BI tool fits the next
audience.