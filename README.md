# Database Intelligence Platform

Internal BI, dynamic reporting and data-quality platform over a strictly read-only connection to a centralized operational database.

**Status:** Phases 2–5 complete (foundation, schema engine, report engine, builder UI, saved reports).
See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and the phase roadmap.

---

## Quick start

Local development runs against **real PostgreSQL**, through the same SELECT-only role production uses. That is deliberate: an earlier SQLite-based setup hid a bug that PostgreSQL rejects outright (see *The fan-out problem* below), so local now matches production dialect and privileges.

```bash
# 1. Python dependencies
python -m venv .venv
.venv/Scripts/python -m pip install -r apps/api/requirements.txt      # Windows
# source .venv/bin/activate && pip install -r apps/api/requirements.txt   # macOS/Linux

# 2. Databases (metadata + a stand-in operational database + redis)
docker compose up -d

# 3. Seed the stand-in operational database (DEV ONLY), then grant reads
.venv/Scripts/python mock-data/seed.py   --url postgresql+psycopg://ops_owner:ops_owner_dev_password@localhost:5434/decoinks_mock
docker exec -i bi_mock_ops_db psql -U ops_owner -d decoinks_mock   -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_readonly;"

# 4. Backend (reads .env, which already points at the containers above)
cd apps/api && ../../.venv/Scripts/python -m uvicorn app.main:app --port 8000 --reload

# 5. Frontend, in a second terminal
cd apps/web && npm install && npm run dev
```

Open <http://localhost:3000>. API docs at <http://localhost:8000/api/docs>.

You should see this on backend startup, and it is the point of the exercise:

```
Read-only self-test passed: the connection cannot write.
```

**Ports:** 3000 web · 8000 API · 5433 metadata DB · 5434 stand-in operational DB · 6379 redis.

The two compose files use distinct project names (`bi-dev`, `bi-prod`) so commands aimed at one stack never touch the other.

### Demo accounts

All use the password `demo1234`. Sign in as different roles to see permissions enforced.

| Email | Role | Can |
|---|---|---|
| `admin@decoinks.local` | Super Admin | everything |
| `boss@decoinks.local` | Management | reports, exports, anomalies |
| `analyst@decoinks.local` | Analyst | build reports, view SQL |
| `viewer@decoinks.local` | Viewer | run reports only |

---

## What works today

- **Schema introspection** — tables, columns, types, primary keys, foreign keys, row estimates; auto-categorized into business areas. Nothing is hardcoded.
- **Dynamic report builder** — data sources, fields, report columns, join diagram, filters, grouping, sorting, column properties, paginated preview.
- **Join planning** — minimum-cost path across the relationship graph, bridge tables pulled in automatically, ambiguity refused rather than guessed.
- **Fan-out correction** — see below.
- **Query safety** — SQLAlchemy Core compilation (no string SQL), `sqlglot` AST gate, row/join/depth limits, read-only transactions.
- **RBAC** — table-level, column-level and masking policies enforced inside the compiler, so every path inherits them.
- **Saved reports** — stored as JSON IR, never as generated SQL.
- **Audit trail** — hash-chained, append-only.

Not built yet: Query Assistant (Phase 7), Anomaly Center (Phase 8), dashboards and Record 360 (Phase 9), scheduling and exports (Phases 6, 10). The navigation shows these disabled with their phase, rather than as dead links.

---

## The fan-out problem

The UI reference this project models contains a real arithmetic bug, and correcting it is the engine's headline feature.

That report joins `Orders → Items → Artworks` and then sums order value, sums item quantity and counts artworks in one flat query. Two one-to-many branches multiply every parent row, so the totals come back inflated. Measured on the seeded data:

```
NAIVE FLAT JOIN                   GROUND TRUTH
Ivan Garcia      325,060.54       McKenzie Nair     32,849.80
Diego Petrov     313,359.64       Aisha Vogel       30,880.29
Omar Haddad      244,823.48       Lena Pacheco      30,025.92
```

Not only are the figures **9.9× too high** — the customer ranking is entirely different. A manager reading that report would act on the wrong names.

The engine detects each row-multiplying branch and rewrites it as a pre-aggregated derived table, so `SUM(orders.total_amount)` stays correct:

```sql
LEFT OUTER JOIN (
  SELECT order_id AS __join_key, SUM(quantity) AS qty
  FROM sales_order_items GROUP BY order_id
) agg_items ON sales_orders.order_id = agg_items.__join_key
```

Where correction would change the report the user actually asked for (they selected detail columns from the branch), the engine warns loudly instead of silently altering results. Branches used only by filters become `EXISTS` checks, which filter without duplicating.

`apps/api/tests/unit/test_report_engine.py::test_screenshot_report_does_not_inflate_totals` pins this behaviour.

---

## Database safety

The application must never be able to modify the operational database. Seven independent layers, detailed in [ARCHITECTURE.md](ARCHITECTURE.md) §D. The two that carry the weight:

**Database privileges.** A dedicated `bi_readonly` role with `default_transaction_read_only = on`. On startup the app attempts a write inside a rolled-back transaction; if that write **succeeds**, it refuses to boot. We do not trust our own code to be the only guard.

**Structural safety.** Reports compile to SQLAlchemy Core expression objects, never strings. Identifiers come only from the schema registry (an allowlist); values only ever bind as parameters. There is no code path from user input to an identifier or an SQL fragment, so injection is not filtered — it is unrepresentable.

Before connecting to production, run the read-only probe:

```bash
.venv/Scripts/python scripts/probe_database.py --json probe_report.json
```

It reports whether the connection is a replica, exactly what the role can do, and what the schema contains — all read-only.

---

## Testing

```bash
cd apps/api && ../../.venv/Scripts/python -m pytest    # 121 tests

# The PostgreSQL suite matters: SQLite once hid a production-breaking bug.
BI_TEST_POSTGRES_URL=postgresql+psycopg://user:pw@host:5432/db   ../../.venv/Scripts/python -m pytest                 # 132 tests

cd apps/web && npm run build                           # typecheck + build
```

| Suite | Tests | Covers |
|---|---|---|
| `test_report_engine.py` | 25 | fan-out correction, join planning, aggregation legality, RBAC, masking, parameters |
| `test_safety.py` | 41 | data-modifying CTEs, stacked statements, forbidden functions, catalog access, allowlists, limits |
| `test_engine_edge_cases.py` | 22 | projection alignment, key collisions, mask bypass via MIN/MAX, NULL semantics, LIKE escaping |
| `test_api.py` | 33 | auth, per-role permissions, injection through HTTP, pagination, save/load round-trip |
| `test_postgres_dialect.py` | 11 | strict GROUP BY, branch re-aggregation, AVG correctness, LIKE escape, masking functions, read-only role |

Injection resistance is asserted on the **parsed AST**, not on substrings — payloads contain SQL keywords, so string matching would prove nothing either way.

Beyond the suite, the engine has been swept with:

- **2,224 combinations** — every filter operator against every column of every discovered type, and every legal aggregation on every column, each compiled *and executed*: 0 failures.
- **400 random multi-table reports** over 2–4 tables: 0 crashes, 0 SQL errors.
- **63 fan-out combinations** with every total checked against independently computed ground truth: 0 inflated.
- **12 concurrent threads × 15 reports**: 0 errors.
- **Full pagination tiling** over 610 rows: 610 collected, 610 unique, no gaps or repeats.

---

## Layout

```
apps/api/          FastAPI backend
  app/domain/      report engine, safety layer, schema registry  <- the core
  app/adapters/    PostgreSQL (production) and SQLite (demo)
  app/api/v1/      routes
  tests/
apps/web/          Next.js frontend
  app/(app)/       authenticated shell and pages
  components/      builder panels, shell, primitives
  store/           report IR state
mock-data/         DEV ONLY: seeder + planted-anomaly manifest
scripts/           read-only production database probe
```

---

## Deploying

See **[DEPLOYMENT.md](DEPLOYMENT.md)**. The stack is nginx → Next.js → FastAPI → metadata PostgreSQL, with the operational database reached read-only from outside the stack.

```bash
cp .env.production.example .env.production   # then fill it in
./deploy.sh                                  # pre-flight checks, build, start, verify
```

`deploy.sh` refuses to start on an empty `APP_SECRET`, a missing database host, or a connection that turns out to be writable. The procedure has been rehearsed end to end against a real PostgreSQL instance with a real `bi_readonly` role.

---

## Connecting the real database

1. Run `scripts/probe_database.py` against it and review the warnings.
2. Have a DBA create the `bi_readonly` role (statements in ARCHITECTURE.md §D/L1).
3. Copy `.env.example` to `.env`, fill in the connection, set `DATA_SOURCE_MODE=live`.
4. Restart. The startup self-test verifies the connection cannot write.
5. Scan the schema under Data Sources, then assign friendly names and categories.

The mock schema is a development convenience only. When the real database is available it is introspected and treated as authoritative — no production field names are assumed anywhere in the codebase.
