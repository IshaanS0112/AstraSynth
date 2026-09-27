# Operations

How the backend runs, what it exposes to whoever is watching it, and what to do
when something goes wrong. The planning and perception maths lives in
[`planners.md`](planners.md), [`uncertainty.md`](uncertainty.md) and
[`architecture.md`](architecture.md); this file is about the *service*.

## Contents

1. [Processes](#processes)
2. [The job queue](#the-job-queue)
3. [Submitting and following work](#submitting-and-following-work)
4. [Schema migrations](#schema-migrations)
5. [Logs](#logs)
6. [Metrics](#metrics)
7. [Health and readiness](#health-and-readiness)
8. [Configuration reference](#configuration-reference)
9. [Runbook](#runbook)

---

## Processes

There are two programs and one database.

```
                         ┌──────────────────────────────┐
  HTTP ───────────────▶   │  app.main (FastAPI)          │
                         │   · request/response          │
                         │   · enqueues long work        │
                         │   · worker threads (default)  │
                         └───────────┬──────────────────┘
                                     │
                            ┌────────▼─────────┐
                            │   PostgreSQL     │
                            │   · missions     │
                            │   · experiments  │
                            │   · jobs  ◀──────┼── the queue is a table
                            └────────▲─────────┘
                                     │
                         ┌───────────┴──────────────────┐
                         │  app.worker (optional)       │
                         │   · same loop, own process    │
                         └──────────────────────────────┘
```

By default the API process runs the worker itself, as one daemon thread
(`WORKER_THREADS=1`). That is deliberate: a single-node research deployment
should not need a second thing started before it works. Because the queue is a
table rather than a broker, moving workers out is a deployment change and not a
code change:

```bash
# API only
WORKER_THREADS=0 uvicorn app.main:app --host 0.0.0.0 --port 8000

# Workers, elsewhere, as many as the box will take
python -m app.worker --workers 4
```

Both arrangements can run *at the same time* — an in-process worker and three
standalone ones are four consumers of one table, and they do not coordinate. See
[The job queue](#the-job-queue) for why that is safe.

The worker is a thread, not an `asyncio` task, and the distinction matters. The
work is CPU-bound numpy and pure-Python graph search; an async task would hold
the event loop for the whole study and stall every request on the process. The
thread exists to own a database session and a lease, not to provide parallelism —
the parallelism inside a Monte Carlo study comes from a real process pool.

`python -m app.worker` handles `SIGTERM` by finishing the job it holds and then
exiting, so a rolling restart does not abandon work mid-study.

---

## The job queue

Four endpoints do enough work that holding a request open for them is wrong: a
Monte Carlo study, a route study across rover configurations, a fleet plan, and a
science tour. They return **202 Accepted** with a job ID.

The queue is the `jobs` table. No Redis, no RabbitMQ. That is a considered
trade, not a shortcut: a broker would add an operational dependency, a second
place for state to live, and a second thing to get wrong in a deployment — to buy
throughput that a workload of a few studies at a time, each measured in seconds,
does not need. PostgreSQL already holds every other durable fact here.

### Claiming is one statement

```sql
UPDATE jobs SET status = 'RUNNING', claimed_by = :worker,
                heartbeat_at = now(), attempts = attempts + 1
 WHERE id = (SELECT id FROM jobs WHERE status = 'QUEUED'
              ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id
```

Not two. "Read the oldest queued row, then update it" is a race — between the
read and the write a second worker reads the same row and the job runs twice.
`FOR UPDATE` makes the read take a lock, and `SKIP LOCKED` is the part that
matters: it stops the second worker *waiting* on that lock and sends it to the
next row instead. Together they give a multi-consumer queue with no broker, no
coordination, and no polling storm.

### Leases, not assignments

A worker that dies holding a job would otherwise leave the row `RUNNING`
forever. So a claim is a **lease**: the worker stamps `heartbeat_at` every two
seconds as it works, and a job whose heartbeat is older than
`JOB_LEASE_SECONDS` (default 90) is returned to the queue by the next worker
that looks.

Reclaiming runs *before every claim* rather than on a timer. A worker looking
for work is exactly the moment when noticing abandoned work is useful, and it
means the recovery path is exercised constantly instead of only during an
incident.

### Delivery is at-least-once

The consequence of a lease is that a job can run twice. That is tolerable here
for a specific reason rather than by hope: **every job kind is deterministic in
its recorded seed**, so a re-run produces the same result rather than a second,
different one. A job kind that was not deterministic would need an idempotency
key before it could go through this queue — that is the bar for adding one.

### Retries

`attempts` is incremented **at claim time**, not at failure time. A worker that
dies without reaching the failure handler still burns an attempt, so a job that
crashes the process cannot spin forever. `max_attempts` defaults to 2. Once
attempts are exhausted, a stale job is buried as `FAILED` with
`worker stopped heartbeating; attempts exhausted` rather than re-queued.

### Cancellation is cooperative

```
QUEUED ──cancel──▶ CANCELLED            (nothing started; nothing to unwind)
RUNNING ──cancel──▶ CANCELLING ──▶ CANCELLED
```

A running job is asked, not killed. The worker learns about it from the return
value of its next heartbeat and stops at a point where it can write a coherent
terminal state. Killing the thread instead would leave a half-written experiment
and no way to say which half.

### Status lifecycle

```
QUEUED ─▶ RUNNING ─▶ SUCCEEDED
   ▲         │
   └─retry───┼─────▶ FAILED          (attempts exhausted, or a hard error)
             └─────▶ CANCELLING ─▶ CANCELLED
```

---

## Submitting and following work

| Work | Synchronous | Queued |
| --- | --- | --- |
| Monte Carlo study | `POST /missions/{id}/monte-carlo` | `POST /missions/{id}/monte-carlo/jobs` |
| Route study | `POST /missions/{id}/route-study` | `POST /missions/{id}/route-study/jobs` |
| Fleet plan | `POST /missions/{id}/fleet-plan` | `POST /missions/{id}/fleet-plan/jobs` |
| Science tour | `POST /missions/{id}/science-tour` | `POST /missions/{id}/science-tour/jobs` |

The synchronous form stays, because a nine-weighting route sweep finishes in
under a second and making a caller poll for that is worse than waiting for it.
The queued form raises the trial cap by two orders of magnitude (50 → 5 000),
because nothing is holding an HTTP connection open any more: the limit becomes
what the machine can finish rather than what a proxy will tolerate.

```bash
# Submit — 202 Accepted with a job, not 200 with a result
curl -s -X POST localhost:8000/missions/$MISSION/monte-carlo/jobs \
  -H 'content-type: application/json' \
  -d '{"start":{"x":40,"y":40},"goal":{"x":460,"y":460},
       "rover_config_id":"'$ROVER'","trials":500,"seed":7}'

# Poll — progress is 0..1, with a short detail line
curl -s localhost:8000/jobs/$JOB
# {"status":"RUNNING","progress":0.42,"progress_detail":"210/500 trials", ...}

# The answer, once status is SUCCEEDED
curl -s localhost:8000/missions/$MISSION/experiments/$(
  curl -s localhost:8000/jobs/$JOB | jq -r .experiment_id)

# Ask it to stop
curl -s -X POST localhost:8000/jobs/$JOB/cancel      # 409 if it already finished

# Every job for one mission, optionally filtered
curl -s "localhost:8000/missions/$MISSION/jobs?status=RUNNING"

# Queue health, for an operator rather than a client
curl -s localhost:8000/jobs
# {"by_status":{"QUEUED":3,"RUNNING":1},"in_process_workers":1,
#  "oldest_queued_seconds":11.4}
```

`GET /jobs/{id}` is deliberately *not* scoped under a mission: a client holding
a job id should not have to remember which mission it came from in order to ask
how it is going. `in_process_workers` is reported beside the depth because the
most common way a queue "breaks" is that nothing is draining it, and a depth on
its own cannot tell a busy system from a stopped one. `oldest_queued_seconds` is
the number to alert on — depth alone is fine if it is moving.

A job's `payload` is the submitted request stored verbatim, so any job can be
inspected or re-run without the caller that submitted it.

---

## Schema migrations

Alembic, with the migration directory at `backend/alembic/`.

```bash
alembic upgrade head          # apply
alembic downgrade -1          # back one
alembic revision --autogenerate -m "add a thing"
```

The API applies outstanding migrations on boot (`AUTO_MIGRATE=true`, the
default), which is right for a single-node deployment and for development. Turn
it off where a deploy pipeline runs `alembic upgrade head` as its own step and
the app should not race it.

Two things are worth knowing about how this is wired:

**A database that predates Alembic is adopted, not rebuilt.** Earlier versions
created tables with `Base.metadata.create_all`, so an existing deployment has the
schema but no `alembic_version` row. On boot, a database with tables and no
version row is **stamped** at head rather than migrated into. That is only safe
because of the next point.

**A test proves the models and the migrations agree.** `tests/test_migrations.py`
runs `alembic upgrade head` against a scratch database, then asks Alembic to
autogenerate a diff against `Base.metadata` and asserts it is empty — with
`compare_type` and `compare_server_default` on, so a changed column type or
default counts as drift. Adding a model field without a migration fails CI. That
test is what makes stamping a legitimate operation rather than a gamble.

---

## Logs

JSON lines by default (`LOG_JSON=true`). The questions worth asking of these
logs — "which requests for this mission took over a second" — are filters over
fields, not regular expressions over prose.

```json
{"ts": "2026-09-27T10:14:02+0000", "level": "INFO",
 "logger": "astrasynth.request", "message": "request",
 "request_id": "3f9c1a7e8b2d4c6a", "method": "POST",
 "route": "/missions/{mission_id}/paths", "status": 200,
 "duration_ms": 412.7}
```

Every line emitted while handling a request carries the same `request_id`,
propagated through a `ContextVar` rather than passed as an argument — so a
service function three layers down logs with correlation without taking a
logging parameter it has no other use for. The same ID goes out on the
`X-Request-ID` response header, so a client can quote it in a bug report, and an
incoming `X-Request-ID` is honoured rather than replaced, so a trace survives a
proxy in front.

Any keyword passed as `extra={"astra_...": value}` rides along into the JSON with
the prefix stripped. That is the whole extension mechanism: a call site can
attach a mission id or a planner's node count without anyone writing a formatter
for it, and the `astra_` prefix is what keeps `logging`'s own record attributes
out of the payload.

Set `LOG_JSON=false` for `asctime level name: message` on a console during
development.

---

## Metrics

Prometheus text exposition at `GET /metrics`.

| Metric | Type | Labels | What it answers |
| --- | --- | --- | --- |
| `astra_http_requests_total` | counter | `route`, `method`, `status` | Traffic and error rate |
| `astra_http_request_seconds` | histogram | `route` | Latency distribution, per route |
| `astra_jobs_total` | counter | `kind`, `outcome` | How much work finished, and how it ended |
| `astra_job_seconds` | histogram | `kind` | How long each kind of study takes |
| `astra_jobs_queue_depth` | gauge | `status` | Backlog — the first number to look at |

Two decisions inside this that are easy to get wrong:

**Labels are route *templates*, not paths.** `/missions/{mission_id}/paths`, not
`/missions/8f3a…/paths`. Labelling by raw path makes cardinality grow with the
number of missions, and a metrics endpoint whose response size grows with your
data is a metrics endpoint that eventually takes the process down. The template
is read off the matched Starlette route in `request.scope`, and a request that
matched nothing is labelled `unmatched` rather than by its path — which is also
what stops a scanner hitting random URLs from inflating the series count.

**Queue depth is derived on scrape, not maintained on write.** It is a property
of a table; counting rows at read time cannot drift from the table, and an
incrementally maintained counter can. If the query fails the scrape still
succeeds without that gauge — a metrics endpoint that 500s during an incident is
worse than one missing a series.

The request middleware sits **outermost**, so it records the final status of
everything including CORS rejections and unhandled exceptions.

---

## Health and readiness

Two endpoints, deliberately not one.

| | Touches the database | Fails when |
| --- | --- | --- |
| `GET /health` | No | The process is wedged |
| `GET /ready` | Yes | The database or storage directory is unreachable (503) |

A liveness probe that fails when the database is down gets the API killed and
restarted *during* a database outage, which helps nobody — the restart cannot fix
the database and the churn removes the one process able to serve a useful error.
Readiness is the check that should fail then, which is why it is separate.

`/ready` returns the checks it ran, so a 503 body says *which* dependency is
missing:

```json
{"database": false, "database_error": "could not connect to server ...",
 "storage": true, "worker_threads": 1, "ready": false}
```

---

## Configuration reference

Everything below is an environment variable, read once at startup by
`app/config.py`. Only the operationally interesting ones are listed; the
perception and planning parameters are documented where they are used.

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://astra:astra@localhost:5432/astrasynth` | JSONB and native UUID are used; SQLite is not an option |
| `AUTO_MIGRATE` | `true` | Run outstanding migrations on boot |
| `WORKER_THREADS` | `1` | `0` when workers run as their own deployment |
| `WORKER_POLL_SECONDS` | `1.0` | How often an idle worker looks for work |
| `JOB_LEASE_SECONDS` | `90.0` | Must comfortably exceed the 2 s heartbeat interval |
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | `true` | `false` for a readable console |
| `STORAGE_DIR` | `backend/storage` | Terrain uploads and rendered overlays; created on startup |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma-separated |
| `ANTHROPIC_API_KEY` | unset | Unset is a supported mode: reports fall back to a deterministic template |

---

## Runbook

**Queue depth climbing, nothing running.** Nothing is consuming. `GET /jobs`
reports `in_process_workers`: if it is 0, either an out-of-process worker is
meant to be running and is not, or `WORKER_THREADS` was set to 0 by accident. If
it is non-zero and `oldest_queued_seconds` keeps growing, the thread is alive but
stuck — `claimed_by` on the `RUNNING` row names the host and PID to look at.

**A job is stuck in `RUNNING` and its worker is gone.** Nothing to do. The next
worker to look for work reclaims any job whose heartbeat is older than
`JOB_LEASE_SECONDS` and re-queues it, or buries it as `FAILED` if its attempts
are spent. `claimed_by` names the host and PID that held it, which is the field
to grep for in the logs.

**A job fails immediately and repeatedly.** `error` on the job row holds the
first 4 000 characters of the traceback. Attempts are counted at claim time, so
it will stop after `max_attempts` rather than looping.

**`/ready` returns 503.** Read the body; it names the failing dependency.
`/health` should still be 200 — if it is not, the process itself is the problem.

**A cancel does not take effect immediately.** Expected. A running job is asked
to stop and notices on its next heartbeat, within about two seconds. A job stuck
in `CANCELLING` past its lease is a dead worker, and is handled by reclaim.

**Migration drift after a model change.** `tests/test_migrations.py` fails with
the autogenerated diff in the assertion message. Generate the revision
(`alembic revision --autogenerate`), read it — autogenerate does not detect
everything, and it is a draft — then commit it with the model change.
