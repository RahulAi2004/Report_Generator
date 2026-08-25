# Deployment

Docker Compose stack: nginx → Next.js → FastAPI → application metadata database.
The operational business database stays where it is and is reached **read-only** over the network.

This procedure has been rehearsed end to end against a real PostgreSQL instance with a real `bi_readonly` role — see "What was verified" at the end.

---

## Deploying on your own server

Assumes a Linux server with Docker and Docker Compose already installed.

```bash
# On the server
git clone https://github.com/RahulAi2004/Report_Generator.git
cd Report_Generator

cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production        # fill in the values -- see section 2 below
```

Generate the three secrets it asks for:

```bash
openssl rand -base64 48     # APP_SECRET
openssl rand -base64 32     # APP_DB_PASSWORD
openssl rand -base64 32     # REDIS_PASSWORD
```

Then follow sections 1 to 6. In short:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f api
```

Startup must print `Read-only self-test passed: the connection cannot write.`
If it does not, the stack refuses to serve and the log says why. That is
working as designed -- fix the cause rather than disabling the check.

### If the operational database runs on the same server

Inside a container `localhost` means the container, not the host. Use:

```
DATABASE_HOST=host.docker.internal
```

`extra_hosts: ["host.docker.internal:host-gateway"]` is already set on the api
service, so this resolves to the host without further changes.

### Choosing a port

`HTTP_PORT` in `.env.production` sets the published port. If port 80 is already
taken by another application on the server, set something free (`HTTP_PORT=8090`)
and put your existing reverse proxy in front of it.

### Updating later

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Named volumes hold the metadata database, so saved reports, users and audit
history survive a rebuild.

---

## Before you start

Have these ready. The first three are blockers.

| Needed | Why |
|---|---|
| Host with Docker Engine + Compose v2, 2 vCPU / 4 GB minimum | Runs the stack |
| Network route from that host to the operational database | The backend must reach it |
| A DBA who can run `CREATE ROLE` on the operational database | The read-only guarantee depends on it |
| A DNS name or fixed IP for users | Cookie and CORS policy |
| TLS certificate (corporate CA or Let's Encrypt) | Anything beyond a closed LAN |

---

## 1. Create the read-only database role

Run this **on the operational database**, as a DBA. Everything else in this document assumes it exists.

```sql
CREATE ROLE bi_readonly LOGIN PASSWORD 'use-a-generated-password';

GRANT CONNECT ON DATABASE <your_database> TO bi_readonly;
GRANT USAGE  ON SCHEMA public TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_readonly;

-- New tables must also be readable, or reports break silently after a release.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bi_readonly;

-- Defence in depth: even a total application compromise cannot write.
ALTER ROLE bi_readonly SET default_transaction_read_only = on;
ALTER ROLE bi_readonly SET statement_timeout = '30s';
ALTER ROLE bi_readonly SET idle_in_transaction_session_timeout = '10s';
```

Verify it yourself before going further:

```bash
psql -U bi_readonly -d <your_database> -c "DELETE FROM <any_table>;"
# expected: ERROR: cannot execute DELETE in a read-only transaction
```

**If a read replica exists, point at the replica instead** and set `DATABASE_IS_REPLICA=true`. Reporting then cannot compete with live operations for I/O at all.

### Survey the database first

```bash
python scripts/probe_database.py --json probe_report.json
```

Read-only. Reports whether you are on a replica, exactly what the role can do, what tables exist and how large they are, and whether foreign keys are declared. Review its warnings before deploying.

---

## 2. Configure

```bash
cp .env.production.example .env.production
chmod 600 .env.production

# Generate the secrets -- do not invent them by hand.
echo "APP_SECRET=$(openssl rand -base64 48)"
echo "APP_DB_PASSWORD=$(openssl rand -base64 32)"
echo "REDIS_PASSWORD=$(openssl rand -base64 32)"
```

Fill in the operational database section with the `bi_readonly` credentials, and set `PUBLIC_ORIGIN` to the address users will type.

**If the operational database runs on the Docker host itself**, use `DATABASE_HOST=host.docker.internal` — inside a container, `localhost` is the container. The compose file already maps that name to the host gateway.

### Capacity

`WEB_CONCURRENCY` is backend worker processes; each holds a pool of up to 10 connections to the operational database. Keep `WEB_CONCURRENCY × 10` comfortably below its `max_connections` — reporting must never starve the business application. Four workers (≈40 connections) suits a few dozen concurrent users.

---

## 3. TLS

For an internal deployment:

```bash
./deploy/generate-cert.sh bi.yourcompany.internal
```

Then in `deploy/nginx.conf`, uncomment the HTTPS server block and change the port-80 block to redirect. For anything reachable from outside the office, use a real certificate — browsers will warn on a self-signed one, which trains users to click through warnings.

---

## 4. Deploy

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

All five services should reach `healthy`. Then confirm the guarantee that matters:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs api | grep "self-test"
# expected: Read-only self-test passed: the connection cannot write.
```

**If the API refuses to start with a read-only violation, that is correct behaviour.** It means the credentials can write to your production database. Fix the role; do not disable the check.

---

## 5. Create the first administrator

Production seeds no accounts, so a fresh install has no way in until you do this.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec api \
  python scripts/bootstrap_admin.py --email you@yourcompany.com --generate-password
```

The password is printed **once**. Store it in your password manager. Then sign in, create the remaining users and assign roles under Settings.

---

## 6. First-run configuration

1. **Data Sources → Scan** — introspects the schema and caches it.
2. Review discovered tables: set friendly names, assign business categories, disable anything that should not be reportable, and flag sensitive columns for masking.
3. Where the database has no foreign keys, define logical relationships. These live in *our* metadata database and never touch production.
4. Build one real report and check its numbers against a known figure before letting anyone else in.

---

## Operations

```bash
# Logs
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f api

# Update to a new version
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build

# Back up application data (saved reports, users, audit trail)
docker compose -f docker-compose.prod.yml --env-file .env.production exec metadata-db \
  pg_dump -U bi_app bi_metadata | gzip > backups/bi_metadata_$(date +%F).sql.gz

# Restore
gunzip -c backups/bi_metadata_2026-08-23.sql.gz | \
  docker compose -f docker-compose.prod.yml --env-file .env.production exec -T metadata-db \
  psql -U bi_app bi_metadata
```

The operational database is never backed up by this stack — it is not ours to touch. Back up `metadata-db`: it holds saved reports, users, permissions and the audit trail.

---

## Security posture

| Layer | Measure |
|---|---|
| Database | Dedicated SELECT-only role, `default_transaction_read_only`, statement timeout |
| Startup | Write probe; the app refuses to boot if the connection can write |
| Query | SQLAlchemy Core compilation (no string SQL), `sqlglot` AST gate, row/join/depth limits |
| Access | Deny-by-default RBAC enforced inside the compiler; column masking; append-only hash-chained audit log |
| Transport | TLS at nginx; CSP, HSTS, `X-Frame-Options`, `nosniff`, `Referrer-Policy` |
| Rate limits | 20 req/s per IP on the API, 6/min on sign-in |
| Containers | Non-root users, read-only root filesystems, `no-new-privileges`, no published database ports |
| Secrets | Env file at `0600`, never logged, never returned by an API |

---

## Troubleshooting

**API exits with a read-only violation** — the credentials can write. Working as designed. Fix the role.

**"The database could not be reached"** — from the host, `psql -h <host> -U bi_readonly -d <db>`. If that works but the container cannot, it is a network path issue: use `host.docker.internal` for a database on the Docker host, and check `pg_hba.conf` allows the container subnet.

**Reports time out** — raise `QUERY_TIMEOUT_SECONDS`, or better, check the operational database has indexes on the columns being filtered and joined. The generated SQL is visible under View SQL for anyone with the permission.

**Everything is slow under load** — raise `WEB_CONCURRENCY`, but confirm the operational database can take the extra connections first.

---

## What was verified

This procedure was rehearsed end to end before being written:

- Both images build; the stack starts and all five services report healthy.
- A real `bi_readonly` role was created with the statements above; `DELETE` was refused, `SELECT` allowed.
- The startup self-test passed in all 8 workers.
- Demo accounts do not exist in production mode; `bootstrap_admin.py` created the only account.
- Schema introspection, report compilation and execution all ran against real PostgreSQL through nginx, and the corrected totals matched values computed independently in `psql`.
- Security headers were confirmed on the wire.

Three bugs were found *by* the rehearsal and fixed: a worker startup race, missing error logging, and — most importantly — the fan-out correction generating SQL that PostgreSQL rejects. That last one had passed every SQLite test. `tests/integration/test_postgres_dialect.py` now guards against its return.
