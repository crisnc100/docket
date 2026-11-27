# ADR 009: host the beta on one machine, with Temporal's dev server

Status: accepted; written on 2026-11-20 after the load run below.
No prediction was committed before the measurement. [ADR 010](010-sandboxes.md) replaced
the nightly reset and the invite codes with private sandboxes and the sweep; the
measurements below were taken with the reset.

## Choice

The hosted beta runs as one Compose project (`compose.prod.yaml`) on a shared host,
behind Caddy. Temporal is its development server, `temporal server start-dev`: one
process that keeps its state in a SQLite file on a volume. Postgres, SeaweedFS (the S3
store), the API, one worker and the `sweep` service run beside it.

The dev server fits this demo:

- Sandboxes live under an hour and the sweep deletes their workflows, so Temporal holds
  about an hour of them.
- One process needs no cluster to run, upgrade or back up.
- The same server runs the local stack and the browser tests, so the beta runs the
  setup the tests cover.

## Measurement

The load run (`scripts/load/beta_load.py`, on an Apple Silicon Mac under Docker Desktop)
sent 60 samples in 10 minutes from four test addresses, made 20 review actions, and
ran one reset by hand halfway through, while `docker stats` was read in a loop. The
worker's model calls went to a local stand-in for the Anthropic API, so the run spent
nothing and still went through the live gateway, the budget ledger and the kill switch.

| Service | Memory limit | Peak | Peak CPU |
| --- | --- | --- | --- |
| temporal | 768 MB | 310 MB | 24% |
| postgres | 512 MB | 73 MB | 4% |
| seaweedfs | 512 MB | 129 MB | 5% |
| api | 512 MB | 260 MB | 19% |
| worker | 768 MB | 276 MB | 17% |
| reset (the service, idle) | 256 MB | 197 MB | 9% |
| reset by hand (its own container) | 256 MB | 179 MB | 50% |

The peaks add up to 1.4 GB against the 3.3 GB of limits. The sum counts the reset
service and the reset run by hand as two containers, and the peaks did not all
happen at the same moment. The `reset` service, at 77% of
its 256 MB, is the closest to its limit; the API is next, at 51% of its 512 MB. Every
sample was accepted, with no 429 or 503. The 31 sent before the reset were deleted by it,
as the reset is meant to do. Of the 29 sent after it, 10 were filed by the rules, 17 were
held and 2 were dismissed by review actions, and none failed. The reset terminated 28 open
workflows and seeded 12 held documents. The stand-in answered 111 model calls, and bills
nothing.

One reset run with `docker compose exec` inside the `reset` service, beside the service's
own process, reached 255 MB of its 256 MB limit. The runbook therefore runs a reset by hand
with `docker compose run --rm`, in a container of its own.

The peaks are from one 10-minute run on one machine. Atlas is x86_64 and runs other
services, so its numbers will differ; the deploy step checks its free memory first.

## Accepted leftovers of a reset

A sample that races the reset can leave two things behind, and the next reset removes
both:

- A sample written just before the reset takes its lock, whose workflow has not started
  when the reset lists the open documents, starts against a deleted row. Its workflow then
  fails in Temporal. No document row is left.
- A sample refused at its document write has already put its PDF in the bucket, so at
  most one `raw/` object per refused sample stays until the next reset.

## What a production setup would need instead (not measured)

None of this is built or measured.

- A Temporal cluster with its own persistence (Postgres or Cassandra) and separate
  frontend, history and matching services, or Temporal Cloud. The dev server keeps no
  replicas, and losing its volume loses every open workflow.
- More than one worker, and an API behind a load balancer, with health-based restarts.
  Compose restarts a process that exits, not one that hangs.
- A managed object store and managed Postgres with point-in-time recovery, in place of
  local volumes and a nightly `pg_dump`.
- Secrets from a secret store, not an env file on the host.
- Alerts on spend, on a stale sweep and on an unhealthy service. The beta has none.
- Per-user accounts and permissions in place of a private sandbox per browser.
