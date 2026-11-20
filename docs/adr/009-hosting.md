# ADR 009: host the beta on one machine, with Temporal's dev server

Status: accepted; written on 2026-11-20 after the load run below.
No prediction was committed before the measurement.

## Choice

The hosted beta runs as one Compose project (`compose.prod.yaml`) on a shared host,
behind Caddy. Temporal is its development server, `temporal server start-dev`: one
process that keeps its state in a SQLite file on a volume. Postgres, SeaweedFS (the S3
store), the API, one worker and the `reset` service run beside it.

The dev server fits this demo:

- The demo resets every night, so Temporal holds at most a day of workflows, a few
  hundred at the beta's caps.
- One process needs no cluster to run, upgrade or back up.
- The same server runs the local stack and the browser tests, so the beta runs the
  setup the tests cover.

## Measurement

The load run (`scripts/load/beta_load.py`, on an Apple Silicon Mac under Docker Desktop)
sent 60 samples in 10 minutes from four test addresses, made 20 review actions, and
ran one reset by hand halfway through, while `docker stats` was read every second. The
worker's model calls went to a local stand-in for the Anthropic API, so the run spent
nothing and still went through the live gateway, the budget ledger and the kill switch.

| Service | Memory limit | Peak | Peak CPU |
| --- | --- | --- | --- |
| temporal | 768 MB | 312 MB | 10% |
| postgres | 512 MB | 73 MB | 4% |
| seaweedfs | 512 MB | 134 MB | 4% |
| api | 512 MB | 232 MB | 24% |
| worker | 768 MB | 233 MB | 15% |
| reset (the service, idle) | 256 MB | 183 MB | 8% |
| reset by hand (its own container) | 256 MB | 153 MB | 50% |

The peaks add up to 1.3 GB against the 3.3 GB of limits. The `reset` service, at 72% of
its 256 MB, is the closest to its limit; every other service stayed under half of its own. Every sample was accepted, with no 429 or 503. The 31 sent before
the reset were deleted by it, as the reset is meant to do. Of the 29 sent after it, 10
were filed by the rules, 18 were held and 1 was dismissed by a review action, and none
failed. The reset terminated 28 open workflows and seeded 12 held documents. The ledger
recorded $0.0038 for the run's answers at Haiku prices; the stand-in bills nothing.

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
- Alerts on spend, on a failed reset and on an unhealthy service. The beta has none.
- Per-user accounts and permissions in place of shared invite codes and one shared
  queue.
