# ADR 010: a private sandbox per browser, deleted within the hour

Status: accepted; written on 2026-11-21 while building it. The Temporal and SeaweedFS
behaviour below was measured on 2026-11-21, before and during the build. No prediction
was committed before the measurements.

## Choice

The hosted beta has no sign-up and no invite codes. Each browser gets a private sandbox
on its first write, named by a cookie of 128 random bits; the database keeps only the
cookie's SHA-256. A sandbox lives 50 minutes. Its documents, their files and their
workflow histories are visible to that browser only, and everything it held is deleted
from Postgres, the S3 store and Temporal within 55 minutes, or at once with "Delete now".

The sandbox is part of a document's identity: the same bytes in two sandboxes are two
documents, two stored files under `sbx/<sandbox>/raw/<sha256>`, and two workflows. A
sandbox document is never copied when it is filed; only where it would be filed is
recorded. So deleting a sandbox leaves no second copy anywhere.

Deletion runs in two places:

- **Delete now** deletes one sandbox from all three stores, then answers.
- **The sweep** runs every 2 minutes, with one pass per store and a 50-second budget per
  pass. Each pass decides what to delete from what is live in Postgres, so a store that
  is down stops only its own pass. If no run completes for 10 minutes, new writes stop
  until one does.

A sandbox lock keeps a write and its deletion apart: every write holds it shared for the
whole write, and Delete now and the sweep take it exclusively. A write that started first
is deleted with the rest; one that comes after writes nothing.

[Operations](../operations.md#hosted-beta-mode) gives the routes, limits and messages.

## Measurement

**Temporal** (the pinned dev server, `temporalio/temporal:1.5.1`, Server 1.29.1):

- It keeps a closed workflow's history for 24 hours, so histories must be deleted, not
  left to expire.
- `DeleteWorkflowExecution` removes a running workflow; `describe` reported it gone 11 ms
  after the call returned. A second delete answers "not found".
- `WorkflowId STARTS_WITH 'doc-' AND TaskQueue = '<queue>'` lists the document workflows
  on one queue, so the sweep sees only Docket's.
- A deleted history's bytes stay readable in the SQLite file unless the server runs with
  `--sqlite-pragma secure_delete=on`, which zeroes them. No `-wal` file exists while the
  server runs.

**SeaweedFS** (4.53, `weed mini`):

- A deleted object stays in its volume file (`/data/docket_<n>.dat`) until the master's
  `/vol/vacuum?garbageThreshold=0.0001`, on port 9333, rewrites the file without it.
- The S3 delete returns before the bytes are freed: the filer frees them from a queue.
  Four trials found the bytes freed 1.0, 1.4, 0.97 and 1.35 seconds after the delete, and
  a vacuum straight after the delete left them in the file. Delete now and the sweep
  therefore wait 3 seconds after deleting objects before they vacuum, and every sweep run
  vacuums, so anything a vacuum came too soon for goes within the next two minutes.
- Two vacuums sent at once both answered 200.

**The marker test** (`tests/browser/test_sandbox_marker.py`) puts a distinct marker in
the stored PDF, the extracted text and the dismiss reason of documents in three
sandboxes, on the compose stack with the beta API and worker in their own containers.
Before deletion, every marker is in `pg_dump`, the stored object, SeaweedFS's data folder
and Temporal's data folder. After one sandbox goes by Delete now and one by the sweep,
their markers are in none of them, and the third sandbox's marker is still in each,
which shows the search would have found them. No container's logs hold any marker. With
the 3-second wait removed from the sweep, the test fails: the swept sandbox's marker stays
in SeaweedFS's volume file.

## Limits

- **Postgres** keeps deleted rows in its files until it reuses the space. The free text
  among them is a dismiss reason, at most 300 characters; the counters hold address
  hashes. `pg_dump` shows none of it.
- **The disk.** SeaweedFS's vacuum and SQLite's `secure_delete` clear the bytes from the
  data folders. Blocks the filesystem freed are not overwritten at the device level.
- **A model call in progress** cannot be recalled. One waiting out a retry delay can
  still send the document's text for a few seconds after Delete now answers.
- **One address, many people.** New sandboxes are limited to 10 an hour per address, an
  IPv6 address counted by its /64, so an office behind one address shares the limit.
  `docket beta-status` shows how many addresses are at it.
- **If the sweep stops,** new writes stop within 10 minutes, but data already in stays
  until the sweep recovers.
- **Backups** would break the hour, so the beta's volumes have none.
- The figures are from an Apple Silicon Mac under Docker Desktop. The host is x86_64, so
  the 1-second free delay may differ there; the 3-second wait and the next run's vacuum
  are the margin.
