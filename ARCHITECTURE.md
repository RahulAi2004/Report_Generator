# Centralized Database Intelligence Platform — Architecture (Phase 1)

**Status:** Proposal — awaiting approval before Phase 2 implementation.
**Date:** 2026-08-23
**Scope:** Internal BI + Dynamic Report Builder + NL Query Assistant + Data Quality / Anomaly Investigation platform, over a strictly read-only connection to the company's centralized operational database.

---

## A. Screenshot Analysis (UI Reference)

The reference is a single-screen, high-density enterprise report builder. Decomposed:

### A.1 Chrome

| Region | Observed | Implementation note |
|---|---|---|
| Left rail | Dark navy (~`#0F2137`), ~80px icon+label, active item filled blue, collapse chevrons top & bottom | Persistent app shell, 9 destinations (spec §4). Collapsible to icon-only. |
| Top bar | Product title, inline-editable Report Name with pencil affordance and required marker, then Save / Save As / Preview / **Run Report** (filled blue, play icon) / kebab | Report Name is a controlled inline edit, not a modal. Primary action is visually dominant. |
| Workflow strip | 6 cards — Data Sources `4 Tables`, Fields Selected `6 Fields`, Relationships `3 Joins`, Filters `2 Filters`, Grouping `1 Group`, Sorting `2 Sorts`; "Last Modified" right-aligned | These are **live counters derived from report state**, and clickable: each focuses its section. Icon + label + count, 1px border, no shadow. |

### A.2 Three-column work area

1. **Data Sources (~200px):** search box, collapsible business categories (Sales / Customers / Artwork / Fulfillment / Purchasing), table rows with type icon, checkbox on selected tables, `Primary` badge pill on the root table.
2. **Fields (~180px):** header scoped to the current table ("Fields – Sales Orders"), field search, checkbox list of physical column names, `+ Add Custom Field` footer.
3. **Center canvas (flex):** stacked panels —
   - **Report Columns** grid: drag handle, `#`, Display Name, Source (`Table.Field`), Aggregation, Data Type, row kebab, `+ Add Column`.
   - **Relationships (Joins)**: horizontal node cards (Customers `Primary` → Sales Orders → Sales Order Items → Artworks) listing PK/FK fields, connected by crow's-foot connectors, with an Inner/Left join legend and `Edit Relationships`.
4. **Column Properties (~340px):** contextual to the selected column — Display Name, Source, Field, Aggregation, Data Type, Format (`$ 1,234.56`), Alignment segmented control, `Visible in Report` checkbox. A `Download` split-button shows an open PDF/CSV menu.

### A.3 Lower zone

- **Filters** (type-aware operator rows with delete, plus `Ask for values when running report` checkbox with info tooltip), **Group By** (drag chips with remove), **Sort By** (field + direction + delete) — three panels on one row.
- **Preview**: "Showing first 50 rows", Refresh Preview, row-limit select, fullscreen toggle, data table with right-aligned numerics/currency, `Total Rows: 234`, paginator `1 2 3 … 24`.

### A.4 Design tokens extracted

Accent `#1B5FDB`; nav `#0F2137`; canvas `#FFFFFF` on page `#F5F7FA`; borders `#E3E8EF` at 1px; body 13px, section labels 11–12px; row height 34–36px; panel padding 16px; radius 6–8px. Icons are 16px line-style, ~1.5px stroke.

### A.5 What we deliberately change

- **Nothing is hardcoded.** Every table, category, field, join, format option and operator is rendered from schema metadata returned by the API.
- The right panel becomes a **tabbed inspector** (Properties / Formatting / Conditional / Formula). The flat list cannot hold the ~14 properties required by §9.
- Filters gain an **AND/OR group tree** (§11). The flat two-row layout cannot express nesting.
- Add a persistent **validation/diagnostics bar** — fan-out warnings, ambiguous join paths, invalid aggregation for type, missing group-by. The reference has nowhere to surface correctness problems, which is exactly where most self-service report bugs live.
- No branding or logos from the reference are reproduced.

### A.6 A correctness problem visible in the reference itself

The example report joins `Customers → Sales Orders → Sales Order Items → Artworks` and then computes `COUNT(Artworks.artwork_id)` **and** `SUM(Sales Order Items.quantity)` **and** `SUM(Sales Orders.total_amount)` in one flat query.

That is arithmetically wrong on any real schema. Joining a one-to-many branch multiplies parent rows, so `SUM(total_amount)` is inflated by the number of joined item/artwork rows, and `SUM(quantity)` is inflated by the artwork fan-out. This is the single most common defect in visual report builders.

**Our engine must detect and correct it** (see §E.4: fan-out detection with automatic pre-aggregated subqueries). This is a headline design requirement, not an edge case.

---

## B. Proposed System Architecture

```
                                  ┌─────────────────────────────┐
                                  │      User Browser (SPA)      │
                                  └──────────────┬───────────────┘
                                                 │ HTTPS · JSON · SSE
                     ┌───────────────────────────▼───────────────────────────┐
                     │  Frontend — Next.js 15 (App Router) · TS · Tailwind    │
                     │  TanStack Query/Table · dnd-kit · React Flow · Recharts│
                     │  (BFF proxy only — NEVER talks to any database)        │
                     └───────────────────────────┬───────────────────────────┘
                                                 │
                     ┌───────────────────────────▼───────────────────────────┐
                     │            Backend API — FastAPI (Python 3.12)         │
                     │  ┌─────────────────────────────────────────────────┐  │
                     │  │ 1. Auth (session/JWT) + RBAC Policy Engine      │  │
                     │  ├─────────────────────────────────────────────────┤  │
                     │  │ 2. Semantic Layer · Schema Metadata Registry    │  │
                     │  ├─────────────────────────────────────────────────┤  │
                     │  │ 3. Report Engine (IR → Join Planner → Compiler) │  │
                     │  ├─────────────────────────────────────────────────┤  │
                     │  │ 4. QUERY SAFETY LAYER (AST validate · governor) │  │
                     │  ├─────────────────────────────────────────────────┤  │
                     │  │ 5. Database Adapter (PG / MySQL / MSSQL)        │  │
                     │  └─────────────────────────────────────────────────┘  │
                     └───┬───────────────┬───────────────┬──────────────┬────┘
                         │               │               │              │
        ┌────────────────▼──┐  ┌─────────▼────────┐  ┌───▼──────────┐  ┌▼─────────────┐
        │ App Metadata DB   │  │ Redis            │  │ AI Query     │  │ Audit Logger │
        │ PostgreSQL (RW)   │  │ cache·queue·rate │  │ Engine       │  │ append-only  │
        │ users, reports,   │  └─────────┬────────┘  │ NL → *IR*    │  │ hash-chained │
        │ rules, anomalies, │            │           │ provider-    │  └──────────────┘
        │ audit, schema md  │  ┌─────────▼────────┐  │ agnostic     │
        └───────────────────┘  │ Worker (Celery)  │  └───┬──────────┘
                               │ exports·schedules│      │ schema metadata ONLY
                               │ anomaly scans    │      │ (no credentials, no rows
                               │ stats · profiling│      │  unless explicitly allowed)
                               └─────────┬────────┘  ┌───▼──────────────────────────┐
                                         │           │ LLM Provider                 │
                                         │           │ Ollama / vLLM / OpenAI-compat│
                                         │           │ / Anthropic — pluggable      │
                                         │           └──────────────────────────────┘
                     ┌───────────────────▼────────────────────┐
                     │  READ-ONLY connection (dedicated role)  │
                     │  ▶ read replica when available          │
                     │  CENTRALIZED OPERATIONAL DATABASE       │
                     └─────────────────────────────────────────┘
```

**Hard rule enforced by topology:** neither the browser nor the AI layer ever holds database credentials, and the only code path that reaches the operational database is `Report Engine → Query Safety Layer → Adapter`. There is no second door.

### Stack decisions

| Concern | Choice | Rationale |
|---|---|---|
| Frontend | Next.js 15, React 19, TS strict, Tailwind, shadcn/ui (Radix) | §39. Radix supplies accessible primitives for the dense control set (menus, popovers, combobox, drawers) without a heavy theme that would read as a generic admin template. |
| Grid | TanStack Table v8 (headless) | Column resize, sticky headers, virtualized preview rows, server-side pagination model. |
| Join canvas | React Flow | Node/edge model maps 1:1 onto our relationship graph and reproduces the card + crow's-foot look. |
| Drag & drop | dnd-kit | Column reorder, group-by/sort chips, field → column drops. |
| Backend | FastAPI, SQLAlchemy 2.0 **Core**, Pydantic v2, Alembic | §39. Core (not ORM) is the compiler target — see §E. |
| SQL AST | `sqlglot` | Multi-dialect parse / validate / transpile; also powers the SQL Inspector formatter. |
| Queue | Celery + Redis behind a `JobQueue` port | §42; the port keeps an RQ/Arq swap cheap. |
| Statistics | numpy, pandas, scikit-learn (IsolationForest only where multivariate) | §23 and the §56 Phase-8 note: deterministic rules first. |
| Metadata DB | PostgreSQL 16 | JSONB for report/rule definitions, partial indexes, `pg_trgm` for fuzzy duplicate matching. |

---

## C. Database Integration Strategy

### C.1 Connection model

Two logically separate pools that are never mixed:

- **`app_engine`** → metadata DB. Read-write, Alembic-migrated, owned by us.
- **`ops_engine[connection_id]`** → operational DB. Read-only, never migrated, never written, no ORM mapping at all.

`ops_engine` configuration: `pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=1800`, `application_name="bi-reporting"` so DBAs can identify our sessions, SSL per configuration.

### C.2 Schema introspection

`POST /api/v1/connections/{id}/schema/scan` runs as a background job and **snapshots** introspection results into the metadata DB. The UI never introspects live.

| Discovered | PostgreSQL | MySQL | SQL Server |
|---|---|---|---|
| tables / views | `information_schema` + `pg_class` | `information_schema` | `sys.tables` / `sys.views` |
| columns, types, nullable, defaults | `information_schema.columns` | same | `sys.columns` + `sys.types` |
| PK / FK / unique | `pg_constraint` | `key_column_usage` | `sys.foreign_keys` |
| indexes | `pg_indexes` | `statistics` | `sys.indexes` |
| approximate row count | `pg_class.reltuples` | `information_schema.tables.table_rows` | `sys.dm_db_partition_stats` |
| comments / descriptions | `obj_description` / `col_description` | `column_comment` | extended properties |

Row counts use **estimates only**. We never run `COUNT(*)` against a production table during a scan.

Every scan produces a **schema version** plus a diff (tables/columns added, removed, type-changed). Saved reports referencing a removed column are marked `needs_attention` rather than failing silently at run time.

### C.3 Logical relationships (§1, §10)

Where the physical database has no foreign keys, admins define logical relationships stored **in the metadata DB**, never in production:

`(left_table, left_column, right_table, right_column, cardinality, default_join_type, confidence, source: physical | manual | inferred)`

Inference heuristics — always *proposed* to an admin, never auto-applied:
1. Column named `<singular_table>_id` where the target table has that primary key.
2. Identical column name where one side is a PK or unique.
3. Name + datatype match confirmed by a sampled value-overlap check (opt-in, `LIMIT 1000`, admin-triggered only).

### C.4 Adapter port

```python
class DatabaseAdapter(Protocol):
    dialect: str
    def introspect(self) -> SchemaSnapshot: ...
    def compile(self, stmt: Select) -> tuple[str, dict]: ...   # dialect SQL + bound params
    def session_guards(self) -> list[str]: ...                 # read-only tx, timeouts
    def apply_limit(self, ast, n: int): ...                    # AST rewrite, not string append
    def explain_cost(self, sql, params) -> float | None: ...
    def cancel(self, backend_id) -> None: ...
    def quote(self, ident: str) -> str: ...
```

Business logic depends only on this protocol. Adding a dialect means adding one module and running the shared conformance suite.

### C.5 Mock / development mode (§50, §51)

`DATA_SOURCE_MODE=mock` provisions a **real PostgreSQL container** seeded from `/mock-data` — not in-memory fixtures. This matters: the mock exercises the same introspection, the same join planner, the same compiler, the same adapter. Moving to production is a connection-string change, not a code change.

The seed includes deliberately planted anomalies (duplicate customer, a $150 invoice/order mismatch, paid-without-payment, an orphan FK, a sales order missing artwork, a 12-day stale production order, a $12,000 outlier on a $200–800 customer), listed in a manifest so integration tests assert exact detection counts. All demo rows carry a `__demo` marker and the UI shows a persistent "Development data" banner in mock mode.

---

## D. Security Strategy — Protecting the Production Database

Seven independent layers. No single failure grants write access.

**L0 — Topology.** Prefer a read replica. Backend in a private network segment; the database is reachable only from the backend. The frontend has zero database reachability.

**L1 — Database privileges (the actual guarantee).** A dedicated role, provisioned by the customer's DBA:

```sql
CREATE ROLE bi_readonly LOGIN PASSWORD '***';
GRANT CONNECT ON DATABASE ops TO bi_readonly;
GRANT USAGE  ON SCHEMA public TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_readonly;
ALTER ROLE bi_readonly SET default_transaction_read_only = on;
ALTER ROLE bi_readonly SET statement_timeout = '30s';
ALTER ROLE bi_readonly SET idle_in_transaction_session_timeout = '10s';
```

Even a total application compromise cannot write. **Startup self-test:** the app attempts a harmless write probe inside a rolled-back transaction; if the probe *succeeds*, the app refuses to start and reports a hard configuration error. We do not trust our own code to be the only guard.

**L2 — Session guards.** Every execution opens `BEGIN TRANSACTION READ ONLY` with `SET LOCAL statement_timeout`. MySQL: `SET SESSION TRANSACTION READ ONLY` plus a `MAX_EXECUTION_TIME` hint. SQL Server: read-only application intent, `LOCK_TIMEOUT`, and a configurable isolation level so reporting never blocks OLTP writers.

**L3 — Structural safety (builder path).** Report definitions compile to **SQLAlchemy Core expression objects**, never strings. Identifiers can only originate from the metadata registry, which is an allowlist; values only ever become bound parameters. Injection is not filtered — it is unrepresentable, because no code path leads from user text to an identifier or an SQL fragment.

**L4 — AST validation (any textual SQL: admin SQL, calculated fields, AI fallback).** Parse with `sqlglot`, then assert:

- exactly one statement; no trailing payload after a semicolon
- root is `SELECT` or `WITH … SELECT`; **no** `Insert/Update/Delete/Drop/Alter/Create/Truncate/Grant/Revoke/Merge/Call/Copy/Set` node anywhere in the tree, including inside CTEs (this blocks data-modifying CTEs, which keyword blocklists miss)
- no `SELECT … INTO`, `INTO OUTFILE`, `COPY … TO`
- every referenced table and column resolves in the **RBAC-filtered** metadata registry; an unknown identifier is a rejection, not a warning
- function allowlist — blocks `pg_read_file`, `pg_sleep`, `dblink`, `lo_import`, `xp_cmdshell`, `LOAD_FILE`, `sp_executesql`
- join count, subquery depth, and cross-join-without-ON limits
- `LIMIT` injected or clamped by AST rewrite, never string concatenation

**L5 — RBAC (§33).** Deny by default. Table-level, column-level, and optional **row-level predicates** injected as mandatory `WHERE` clauses at compile time. Masking policies (`redact | partial | hash | null`) applied in the projection. Enforced inside the compiler, so every path — builder, AI, anomaly rule, export, scheduled run — inherits it. There is no "AI bypass", because the AI never emits SQL (see §F).

**L6 — Resource governor.** Per query: timeout, max rows, max joins, max result cells, and a PostgreSQL `EXPLAIN` cost ceiling checked before execution. Per user: concurrent-query cap and a Redis token-bucket rate limit. Cancellation via backend PID on client disconnect. Exports beyond a threshold are forced into worker jobs using server-side cursors, streamed to disk, never fully materialized in memory.

**L7 — Audit (§34, §35).** Append-only `audit_log`; the application role holds no UPDATE/DELETE grant on it; rows are hash-chained (`prev_hash`) so tampering is detectable. Secrets are never logged. Connection passwords are encrypted at rest with envelope encryption (AES-GCM, key derived from `APP_SECRET`), decrypted only inside the adapter, and **never** returned by any API — the connection endpoint returns `password: null` with `has_password: true`.

**Application security.** Argon2id password hashing; httpOnly + SameSite=Strict session cookies; CSRF double-submit on state-changing routes; strict CSP; Pydantic validation on every input; React output encoding; idle + absolute session expiry; secrets from environment or a secret manager; `.env.example` containing no real values.

---

### D.x Credential columns are excluded in code, not by configuration

Column sensitivity is otherwise an administrator's decision: which columns are sensitive, who may see them, whether they are masked. Credentials are the exception. A password hash, a session token or an API key has no reporting use at all, and one in a downloaded spreadsheet cannot be taken back — so `CREDENTIAL_COLUMNS` is applied in `SchemaRegistry.for_principal()` before any configuration is consulted, and there is no setting that turns it back on.

Excluded rather than masked: a masked hash still appears in the field list, still confirms the account exists, and still invites someone to add it to a report.

Matched on the whole column name, so `password_changed_at` remains reportable. Over-matching has a cost too — it silently removes data people have real reasons to report on.

---

## E. Report Engine Design — How Visual Configuration Becomes Validated SQL

Six stages. Each is independently unit-testable and none of them concatenates SQL.

```
Report Definition (JSON/IR)
   → 1. Resolve   (identifiers → metadata registry objects, RBAC filtered)
   → 2. Plan      (join graph traversal → ordered join tree, fan-out analysis)
   → 3. Validate  (semantics: aggregation/type, group-by completeness, cardinality)
   → 4. Compile   (SQLAlchemy Core Select, bound params only)
   → 5. Govern    (limit, timeout, cost pre-flight, RBAC row predicates)
   → 6. Execute   (adapter, read-only tx, paginate/stream) → rows + metadata
```

### E.1 The IR (what we persist — §16 requires config, not SQL)

```jsonc
{
  "version": 1,
  "connection_id": "uuid",
  "primary_table": "sales_orders",
  "tables": ["customers", "sales_orders", "sales_order_items", "artworks"],
  "joins": [
    { "left": "customers", "left_col": "customer_id",
      "right": "sales_orders", "right_col": "customer_id",
      "type": "inner", "relationship_id": "uuid", "cardinality": "1:N" }
  ],
  "columns": [
    { "id": "c1", "table": "sales_orders", "field": "order_no",
      "display_name": "Sales Order No.", "aggregation": "none",
      "data_type": "text", "format": null, "align": "left",
      "visible": true, "width": 140 },
    { "id": "c5", "table": "sales_order_items", "field": "quantity",
      "display_name": "Transfers Qty", "aggregation": "sum",
      "data_type": "number", "format": { "kind": "number", "decimals": 0 } }
  ],
  "calculated_columns": [
    { "id": "cc1", "display_name": "Outstanding",
      "expression": "invoices.total_amount - SUM(payments.amount)" }
  ],
  "filters": {
    "op": "and",
    "children": [
      { "table": "sales_orders", "field": "order_date", "operator": "between",
        "values": ["2026-05-01", "2026-05-31"], "parameter": null },
      { "table": "sales_orders", "field": "status", "operator": "in",
        "values": ["Paid", "In Production", "Shipped"],
        "parameter": { "name": "p_status", "prompt": "Status", "required": false } }
    ]
  },
  "group_by": [{ "table": "customers", "field": "customer_name" }],
  "sort_by": [
    { "column_id": "c3", "direction": "desc" },
    { "column_id": "c6", "direction": "desc" }
  ],
  "visualization": { "type": "table" },
  "row_limit": 50
}
```

Every UI panel in the screenshot is a view over one branch of this document. The workflow counter cards are derived: `tables.length`, `columns.length`, `joins.length`, filter leaf count, `group_by.length`, `sort_by.length`.

### E.2 Resolution

Each `{table, field}` is looked up in the schema registry for the report's connection, filtered by the caller's RBAC grants. An unresolvable or unauthorized reference fails the whole compile with a specific, user-readable error. Because resolution returns typed metadata objects, downstream stages work with types, not strings.

### E.3 Join planning

Tables are nodes; physical and logical relationships are weighted edges (physical FK cheaper than manual, manual cheaper than inferred). Given the selected tables and the designated primary table:

- BFS/Dijkstra to a minimum-cost connecting tree (Steiner-tree approximation for the multi-table case).
- **No path** → error naming the disconnected tables, with a "define a relationship" call to action.
- **Multiple equal-cost paths** → ambiguity: refuse to guess, present the candidate paths, let the user choose (persisted in `joins`).
- **Cycle** → break at the highest-cost edge and warn.
- Join type per edge, defaulting to LEFT when the child side is optional and INNER when the FK is NOT NULL. Analysts can override; other join types are admin-gated (§10).
- Cartesian products are structurally impossible: every join carries an `ON` from a registered relationship.

### E.4 Fan-out analysis — the correctness feature

Walking the join tree from the primary table, any `1:N` edge marks a **fan-out branch**. If aggregates are requested across two or more distinct fan-out branches, or a non-additive aggregate sits above a fan-out, a flat query silently produces inflated numbers.

The planner rewrites those branches as **pre-aggregated derived tables**:

```sql
LEFT JOIN (
  SELECT order_id, SUM(quantity) AS qty_sum
  FROM sales_order_items GROUP BY order_id
) soi ON soi.order_id = so.order_id
LEFT JOIN (
  SELECT order_id, COUNT(DISTINCT artwork_id) AS artwork_count
  FROM artworks GROUP BY order_id
) aw ON aw.order_id = so.order_id
```

so `SUM(so.total_amount)` stays correct. When automatic rewriting is impossible, the diagnostics bar warns explicitly rather than returning quietly wrong numbers — this is the difference between a BI tool management can trust and one they cannot.

### E.5 Semantic validation

- Aggregation must be legal for the column type (`SUM` on text is rejected; only `COUNT`/`COUNT DISTINCT`/`MIN`/`MAX` are offered for text and dates). The API returns the legal set per column so the UI never renders an invalid option (§8).
- Mixed aggregate and non-aggregate columns require every non-aggregate to appear in `GROUP BY`; the builder offers a one-click fix.
- Sort columns must exist in the projection or be group keys.
- Filters on aggregates are routed to `HAVING`, not `WHERE`.
- Calculated fields (§46) are parsed by a restricted grammar (Lark) with an allowlisted function set, resolved against real columns, and compiled to expression objects — no raw SQL from non-admins, ever.

### E.6 Compilation and execution

The compiler emits a SQLAlchemy Core `Select`. Parameters bind; identifiers come only from registry objects. Then the governor clamps `LIMIT`, attaches RBAC row predicates, and runs an `EXPLAIN` cost pre-flight where the dialect supports it. Execution happens in a read-only transaction with a statement timeout, returning `{columns, rows, total_count?, duration_ms, sql, params_redacted, truncated}`.

Pagination is server-side keyset where a stable ordering exists, offset otherwise. Total count is computed as a separate cheap query only when the estimated cost is acceptable — otherwise the UI shows "50+ rows" rather than stalling the preview.

### E.7 SQL Inspector (§45)

Authorized roles see the formatted, read-only generated SQL (`sqlglot` pretty-print) plus tables used, join list, filter list, row count and execution time. Bound values are shown as placeholders unless the user holds the `view_query_values` permission.

---

## E-bis. Dashboard Layer — Metric Cards, Shared Filters, Embedded Reports

A dashboard is a saved definition, never SQL, for the same reason a report is not (§16). It carries its placement, a time range, metric cards, dashboard-level filters, embedded report panels, and viewer settings.

### The central decision: dashboards compile down to reports

A metric card is a report with one aggregated column and no grouping. An embedded panel is a saved report with the dashboard's filters added. Both are translated into ordinary `ReportDefinition` documents by `app/domain/dashboard/builder.py` and handed to the report engine.

Nothing in the dashboard layer touches a database. That is the point: dashboards inherit column masking, the read-only guard, parameterisation, join planning and fan-out correction because they *use* the report path rather than paralleling it. There is one place in this application where a query is built, and adding dashboards did not make it two.

### The window is stored relative, not resolved

`TimeRange` holds `preset` + `mode` + `periods` ("last 30 days"), not two dates. A dashboard saved in August still reports the last thirty days when opened in March; a resolved range would go stale while continuing to look current. Dates are computed per request, once, so a dashboard rendered across midnight does not measure its cards over two different windows.

The comparison window ("vs previous period") is the same length as the current one and adjacent to it rather than overlapping — an overlap would count the same days on both sides of the comparison. The comparison report differs from the current one *only* in its window, so the percentage measures the period and nothing else.

A window is meaningless without a column to measure it against, so a time range names a date field, and a card may override it. `suggest_date_field()` prefers a business date (`order_date`, `invoice_date`) over a row-creation timestamp: an order placed in June and entered in July belongs to June on any report a person would recognise.

### Filters report what they actually did

This is the failure mode the layer is engineered against: a dashboard whose chips claim more than its queries did.

- A filter whose table a panel does not read **cannot** apply. It is returned in `not_applicable` and rendered struck through — not hidden. A missing chip reads as "no filter" and a present one reads as "filtered"; neither is true.
- A filter naming a table a metric card does not otherwise read pulls that table in, so "Status: Delivered" means the same thing on every card rather than silently meaning different things.
- A filter with no value is "All": a control that is present but not narrowing. It is neither applied nor reported as inapplicable.
- A panel with no date column to measure reports `time_range_applied: false`, so it can say it is showing all time while its neighbours show a period.
- A card that cannot be computed fails alone. One broken card does not take the dashboard down.

### Captions are computed, never typed

The line under a number ("vs previous 30 days", "8.9% of 123 total") comes from the server alongside the value, so it cannot drift away from the figure above it. A percentage against a base of zero is reported as a direction and a difference rather than as infinite growth — it is undefined, not large.

### Schema evolution

Table names saved before a second schema was exposed still resolve. Qualification only happens on collision, so a bare name that no longer resolves names a table that has acquired a namesake rather than one that has gone; the registry falls back to the physical name, resolving to the first schema configured. This is applied in `SchemaRegistry.table()` — one place every caller goes through — and a report's declared joins are canonicalised the same way, since resolving the table list alone leaves a report's own join choices pointing at names the planner no longer recognises.

---

## F. Anomaly Engine Design

Two clearly separated detector families, sharing one anomaly store, one investigation workflow, and — critically — the same compiler and safety layer as reports. Anomaly rules cannot reach the database any way a report cannot.

### F.1 Rule-based detection (§22)

Rules are **data**, not code — stored as JSON in the metadata DB and authored in the Rule Builder (§29). Each rule declares a detector kind, and each kind is a small compiler that emits the same IR the report engine consumes:

| Kind | Emits | Covers |
|---|---|---|
| `comparison` | join + predicate with tolerance | invoice total ≠ order total ± tolerance |
| `existence` | anti-join (`LEFT JOIN … WHERE right IS NULL`) | paid without payment; invoice without sales order; order without items; order without artwork |
| `aggregate_comparison` | grouped subquery + HAVING | `SUM(payments) ≠ invoice.total`; overpayment; underpayment |
| `orphan_fk` | anti-join on any relationship | child referencing missing parent |
| `duplicate` | self-join / window over a normalized key | duplicate customer (normalized email, phone digits, name trigram, address); duplicate invoice number |
| `temporal_order` | date comparison predicate | `invoice_date < quotation_date`; `shipment_date < order_date`; `updated_at < created_at` |
| `status_contradiction` | configurable state matrix | Cancelled order + Shipped shipment; Paid order + no payment |
| `staleness` | status + age threshold | "In Production" longer than N days |
| `missing_data` | null / empty predicate set | customer without contact; shipment without tracking; payment without method |
| `range` | numeric bounds | zero, negative, or implausibly large totals and quantities |

Every rule carries `severity`, `confidence`, `tolerance`, `enabled`, `schedule`, `evidence_columns`, and a **message template** — so §28 explainability is produced by construction, not written by hand per anomaly.

Fuzzy duplicate matching uses `pg_trgm` similarity computed **in the metadata DB** on extracted candidate keys, so we never push expensive similarity work onto the production database.

### F.2 Statistical detection (§23)

Runs in the worker, never inline with a page request:

- **Robust z-score (median/MAD)** and **IQR** per cohort — per customer, per product, per supplier — rather than global thresholds, so a large customer is not permanently flagged.
- **Baseline comparison** against rolling windows for daily sales, order counts, per-employee transaction volume.
- **Isolation Forest** only where the signal is genuinely multivariate (amount × quantity × item count × customer tenure). Where a deterministic rule works, we use the rule — per §56 Phase 8.
- Baselines are stored in the metadata DB and refreshed on schedule; scoring reads aggregates, not raw table scans.

Results are labeled **`STATISTICAL_ANOMALY`** and rendered in a visually distinct track from **`BUSINESS_RULE_VIOLATION`**. The UI states plainly that a statistical anomaly is an outlier, not an error.

### F.3 Scan lifecycle

```
Scheduler → scan job → for each enabled rule:
    compile IR → safety layer → execute (read-only, LIMIT capped)
    → for each returned row: fingerprint = sha256(rule_id, entity_type, entity_key)
    → upsert into detected_anomalies:
         new fingerprint            → status=New
         existing + still failing   → touch last_seen_at, keep human status
         existing + no longer fails → status=Auto-Resolved (with timestamp)
```

Fingerprinting is what prevents the alert fatigue §32 warns about: a scan re-run does not recreate anomalies an analyst already triaged as False Positive. Scans are incremental where a reliable `updated_at` watermark exists, full otherwise, and always resource-governed.

### F.4 Evidence and explainability (§28)

Each anomaly persists structured evidence: `expected_value`, `actual_value`, `difference`, the evidence column values, related record keys, and the rule definition snapshot **as of detection time** (so later rule edits don't rewrite history). The UI renders four fixed sections — What happened / Why it was flagged / Related records / Detection rule — plus one-click pivots into the Record 360 view and the Reconciliation trace.

### F.5 Reconciliation & Record 360 (§24, §25)

Both are the same primitive: a **traversal of the relationship graph** from a seed record, breadth-first to a configurable depth, collecting related rows per hop with per-table row caps. Because it walks the discovered graph rather than a hardcoded Lead→Quotation→…→Shipment chain, it works on the real schema whatever its shape. The pipeline stages shown in the UI come from a configurable "business flow" definition in the semantic layer, which maps discovered tables onto stages.

---

## G. AI Query Engine (§18–§20) — Key Decision

**The LLM does not generate SQL. It generates the Report IR.**

```
Question → Retrieve schema context (RBAC-filtered, semantic layer, synonyms, few-shot)
        → LLM emits IR JSON, constrained by JSON Schema
        → Pydantic validation → resolve identifiers → confidence scoring
        → low confidence / ambiguity → clarification dialog (do not guess)
        → SAME report compiler → SAME safety layer → execute
        → results + plain-English explanation + optional SQL panel
```

Consequences:
- Prompt injection cannot produce dangerous SQL, because the model has no channel that carries SQL. The worst it can do is request an unauthorized table — which the resolver rejects.
- RBAC is inherited automatically; there is no separate AI permission path to get wrong.
- The AI benefits from fan-out correction, formatting, and governance for free.
- A narrow, admin-gated `constrained_sql` fallback exists for questions the IR cannot express; it goes through the full L4 AST gate and is disabled by default.

**Confidence scoring** combines identifier-resolution coverage, semantic-layer term matches, ambiguity detection (a term mapping to multiple metrics — e.g. "unpaid" → `payment_status != 'Paid'` vs `paid_amount < invoice_total`), and self-consistency across n=3 samples. Below threshold, the system asks rather than guesses, exactly as §19 requires.

**Provider abstraction:** `LLMProvider.complete(messages, schema) -> dict` with implementations for Ollama, vLLM, any OpenAI-compatible endpoint, and Anthropic. Configured per environment. The AI layer receives schema metadata and semantic definitions only — never credentials, and never row data unless an admin enables sample-value grounding for a non-sensitive column.

---

## H. Folder Structure

```
report-generator/
├─ apps/
│  ├─ web/                              # Next.js 15
│  │  ├─ app/(auth)/login/
│  │  ├─ app/(app)/dashboard|reports|query-assistant|anomalies|
│  │  │            data-sources|schedules|templates|audit-logs|settings/
│  │  ├─ components/
│  │  │  ├─ shell/            # Sidebar, Topbar, CommandPalette, GlobalSearch
│  │  │  ├─ report-builder/   # WorkflowCards, DataSourcePanel, FieldPanel,
│  │  │  │                    # ColumnGrid, ColumnInspector, JoinCanvas,
│  │  │  │                    # FilterTree, GroupByPanel, SortPanel, PreviewTable
│  │  │  ├─ anomaly/          # AnomalyTable, AnomalyDetail, EvidenceCard, RuleBuilder
│  │  │  ├─ query-assistant/  # AskBar, InterpretationCard, ClarifyDialog, SqlPanel
│  │  │  ├─ charts/  ├─ data-table/  └─ ui/     # shadcn primitives
│  │  ├─ lib/  (api-client, report-ir types, formatters, permissions)
│  │  └─ stores/ (report-builder store — the IR lives here)
│  ├─ api/                              # FastAPI
│  │  └─ app/
│  │     ├─ main.py  core/ (config, security, errors, logging, deps)
│  │     ├─ api/v1/  (auth, connections, schema, reports, query, ai,
│  │     │            anomalies, rules, exports, schedules, audit, search, admin)
│  │     ├─ domain/
│  │     │  ├─ report/     ir.py resolver.py join_planner.py fanout.py
│  │     │  │              validator.py compiler.py formatter.py
│  │     │  ├─ safety/     ast_guard.py governor.py rbac_filter.py masking.py
│  │     │  ├─ schema/     introspector.py registry.py inference.py diff.py
│  │     │  ├─ semantic/   metrics.py business_flow.py synonyms.py
│  │     │  ├─ anomaly/    rules/ (comparison, existence, aggregate, duplicate,
│  │     │  │              orphan, temporal, status, staleness, missing, range)
│  │     │  │              statistical/ (zscore, iqr, baseline, isolation_forest)
│  │     │  │              engine.py fingerprint.py evidence.py
│  │     │  ├─ ai/         provider/ (base, ollama, vllm, openai_compat, anthropic)
│  │     │  │              context_builder.py ir_generator.py confidence.py explain.py
│  │     │  ├─ reconcile/  graph_walker.py record360.py
│  │     │  └─ rbac/       policy.py permissions.py
│  │     ├─ adapters/      base.py postgres.py mysql.py mssql.py
│  │     ├─ models/        # metadata DB SQLAlchemy models
│  │     ├─ repositories/  services/  schemas/  # Pydantic DTOs
│  │     └─ migrations/    # Alembic
│  └─ worker/              tasks/ (exports, scheduled_reports, anomaly_scan,
│                                  statistical, schema_refresh, profiling)
├─ packages/shared-types/  # IR + DTO types generated from OpenAPI → TS
├─ mock-data/              # DEV ONLY: schema.sql, seed.py, anomaly_manifest.json
├─ tests/                  unit/ integration/ e2e/ security/
├─ docs/                   ARCHITECTURE DATABASE_INTEGRATION REPORT_ENGINE
│                          ANOMALY_ENGINE SECURITY DEPLOYMENT AI_QUERY_ENGINE
├─ docker-compose.yml  .env.example  README.md
```

Rules: no SQL in UI components; no business logic in route handlers (they orchestrate services); one responsibility per module; every domain module independently testable without a database where possible.

---

## I. Application Metadata Models (§40)

Never placed in the operational database.

**Identity & access** — `users`, `roles`, `user_roles`, `permissions`, `role_permissions`, `table_permissions` (role × table × allow/deny), `column_permissions` (+ masking policy), `row_policies` (role × table × predicate), `sessions`.

**Connections & schema** — `db_connections` (encrypted secret, `is_read_only`, `is_replica`), `schema_versions`, `schema_tables` (physical + friendly name, category, description, `enabled_for_reporting`, `enabled_for_ai`, `is_sensitive`, estimated rows), `schema_columns` (type, nullable, PK/FK flags, friendly name, format defaults, `is_sensitive`), `relationships` (physical | manual | inferred, cardinality, confidence), `table_categories`.

**Semantic layer** — `metrics` (name, definition IR, description, owner), `dimensions`, `synonyms`, `business_flows` (stage → table mapping for reconciliation).

**Reporting** — `reports` (definition JSONB, owner, folder, favorite, archived), `report_versions`, `report_shares`, `report_templates`, `report_parameters`, `report_runs`, `export_jobs`, `schedules`, `schedule_runs`.

**Query & AI** — `query_history` (user, timestamp, generated SQL, tables accessed, duration, rows, status, error), `ai_conversations`, `ai_queries` (question, interpretation, IR, confidence, clarifications, feedback).

**Anomalies** — `anomaly_rules` (definition JSONB, severity, tolerance, schedule, enabled), `rule_versions`, `detected_anomalies` (fingerprint, category, severity, confidence, entity ref, expected/actual, evidence JSONB, rule snapshot, status, assignee, first_seen, last_seen, resolved_by, resolved_at), `anomaly_comments`, `anomaly_scans`, `statistical_baselines`, `alert_configs`, `alert_deliveries`.

**Platform** — `audit_log` (append-only, hash-chained), `app_settings`, `notifications`, `data_profiles`.

---

## J. Major API Endpoints (v1)

```
Auth        POST /auth/login · /auth/logout · GET /auth/me · POST /auth/refresh

Connections GET|POST /connections · GET|PATCH|DELETE /connections/{id}
            POST /connections/test          POST /connections/{id}/schema/scan
            GET  /connections/{id}/schema/status

Schema      GET  /schema/tables?category=&search=      GET /schema/tables/{id}/columns
            PATCH /schema/tables/{id}                  PATCH /schema/columns/{id}
            GET|POST /schema/relationships             POST /schema/relationships/infer
            GET  /schema/categories                    GET /schema/diff/{version}

Reports     GET|POST /reports · GET|PUT|DELETE /reports/{id}
            POST /reports/{id}/duplicate · /archive · /favorite · /share
            POST /reports/validate        # IR → diagnostics, no execution
            POST /reports/preview         # IR → paginated rows
            POST /reports/{id}/run        POST /reports/{id}/sql   # inspector
            POST /reports/{id}/export     GET  /exports/{job_id}

Dashboards  GET|POST /dashboards · GET|PUT|DELETE /dashboards/{id}
            POST /dashboards/preview      # every metric card, with what each measured
            POST /dashboards/panel        # one embedded report, dashboard filters applied
            GET  /dashboards/options      # apps, modules, saved reports, window sizes
            GET  /dashboards/suggest-date-field?table=

Templates   GET|POST /templates · POST /templates/{id}/instantiate

Query AI    POST /ai/ask            → interpretation + confidence (+ clarifications)
            POST /ai/clarify        POST /ai/execute        POST /ai/explain
            GET  /ai/history        POST /ai/feedback

Anomalies   GET  /anomalies?severity=&status=&category=&table=&from=&to=
            GET  /anomalies/{id} · PATCH /anomalies/{id}   # status, assignee
            POST /anomalies/{id}/comments      GET /anomalies/stats
            GET|POST /anomaly-rules · PUT|DELETE /anomaly-rules/{id}
            POST /anomaly-rules/{id}/test      POST /anomaly-scans

Reconcile   GET  /trace?identifier=SO-10248     GET /record360/{entity}/{id}

Dashboard   GET  /dashboard/kpis · /dashboard/charts/{chart_key}
Search      GET  /search?q=
Schedules   GET|POST /schedules · POST /schedules/{id}/run-now · GET /schedules/{id}/runs
Audit       GET  /audit-logs        GET /query-history
Admin       GET|POST /users · /roles · /permissions · GET|PATCH /settings
Profiling   POST /profiling/tables/{id}   GET /profiling/{job_id}
```

All list endpoints: cursor pagination, RBAC-filtered, audited.

---

## K. Development Roadmap

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **1. Architecture** *(this document)* | Architecture, IR spec, security model, folder structure | Approved by you |
| **2. Foundation** | Monorepo, Docker compose, metadata DB + migrations, auth, RBAC skeleton, app shell + navigation, design tokens, mock Postgres seeded | Log in, navigate all 9 sections, shells render |
| **3. Schema Engine** | Adapters, introspection, snapshot/versioning, relationship discovery + manual editor, Schema Explorer admin UI, categories, friendly names | Scan mock DB, browse discovered schema, define a logical relationship |
| **4. Report Engine** | IR, resolver, join planner + fan-out correction, validator, compiler, safety layer, governor, preview | Build the reference report visually; preview returns **correct, non-inflated** numbers |
| **5. Builder UI** | Full screenshot-parity builder: data sources, fields, column grid, inspector, join canvas, filter tree, group/sort, preview, diagnostics bar | Reference screen reproduced, entirely metadata-driven |
| **6. Saved Reports & Export** | Save/Save As/duplicate/archive/share/favorite, templates, parameters, CSV/XLSX/PDF/JSON via worker with status | Save, reopen, run with parameters, export 100k rows without blocking |
| **7. Query Assistant** | Provider abstraction, context builder, NL→IR, confidence, clarification, explanation, SQL panel, history | The §18 question list answered correctly on mock data |
| **8. Anomaly Engine** | Rule kinds, scan lifecycle, fingerprinting, evidence, Anomaly Center UI, rule builder, investigation workflow | Every planted anomaly in the manifest detected with correct evidence; no duplicates on rescan |
| **9. Statistical + Dashboards** | Robust z-score/IQR/baselines/IsolationForest, executive dashboard, anomaly dashboard, Record 360, reconciliation trace, global search | Trace `SO-10248` end-to-end; dashboards on real aggregates only |
| **10. Scheduling, Alerting, Hardening** | Schedules + run history, alerting with cooldown/dedup/ack, data profiling, security review, load test, docs set, E2E suite | Acceptance criteria §60 items 1–17 demonstrably pass |

Each phase ends with tests and a runnable increment. Nothing is merged without the safety-layer test suite green.

---

## L. Assumptions (each isolated so it can be replaced)

| # | `ASSUMPTION` | Isolated in | Replace by |
|---|---|---|---|
| A1 | Operational DB is **PostgreSQL** | `adapters/postgres.py` | Answer to Q1 → build the right adapter first |
| A2 | Every business table has a single-column surrogate PK | `join_planner`, `reconcile` | Composite-key support behind the same interface |
| A3 | Tables carry `created_at` / `updated_at` for staleness and incremental scans | `anomaly/staleness`, scan watermark | Fall back to full scans + status-history tables |
| A4 | Currency is single-currency per record; no FX conversion in MVP | `formatter`, `metrics` | Add FX table to semantic layer |
| A5 | No existing SSO; we own authentication | `core/security`, `api/v1/auth` | Swap in OIDC/LDAP provider |
| A6 | A new PostgreSQL instance may be provisioned for metadata | `docker-compose`, `app_engine` | Any RW Postgres reachable from backend |
| A7 | Business flow is Lead→Quotation→Order→Items→Artwork→Invoice→Payment→Production→Shipment | `semantic/business_flow` (config row, not code) | Reconfigure stage→table mapping |
| A8 | Deployment is internal network, Docker | `docs/DEPLOYMENT.md` | Answer to Q4 |

The mock schema is a **development convenience only**. When the real database is available we introspect it and treat its schema as authoritative; no production field names are invented anywhere in the codebase.
