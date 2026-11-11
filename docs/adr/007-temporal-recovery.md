# ADR 007: use Temporal to resume document work

Status: accepted; written on 2026-11-11 after the fault-suite measurements.
The implementation and results predate this record; no advance prediction is claimed.

## Choice

Use Temporal to save workflow progress, retry steps and wait for human review.
A workflow is the saved sequence for one document.
An activity is a step run by a worker, such as extraction or storage.
Activities may run more than once after a crash.

The ledger is the database record of document state and events.
Database uniqueness and repeat-safe writes protect effects when steps repeat.
The raw bytes are saved before the document row and workflow start.
A reconciler starts workflows missing after an API crash.
Storage uses a content hash, a digest of the file bytes, and refuses different bytes at the same key.
A filing row and each event have unique keys.
These constraints matter as much as workflow recovery.

## Measurement

The five [fault-suite cases](../../tests/fault/test_faults.py) run against real Compose services.
The [recorded PR3 run](../../builds/v01/pr3-fix-result.md) passed 609 tests including these five,
with zero skips.
A failpoint is a named place where a test deliberately exits a process.

| Fault | Observed check |
| --- | --- |
| Worker exits after storage, before the ledger write | Restart reaches filed with one filing object, one filing row and one filed event |
| Worker exits in choose before the gateway call | Restart reaches the same single-filing outcome |
| API exits after raw storage | Resubmission recovers one document and one filing |
| API exits after document insertion, before workflow start | Reconciliation starts the workflow and it files once |
| SeaweedFS storage stops after a confirmed worker exit | A real connection error occurs; restart within the retry window produces one filing |

The crash checks require exit code 137 and the matching failpoint log.
The worker-crash cases send three concurrent uploads and a later resubmission with a different message id.
They assert one document and four source entries, not four filings.

This measures exactly-once effects in those cases, not exactly-once execution.
Steps can repeat. It does not show that a model is billed only once after a lost response.
The choose failpoint is before the gateway call, so the suite does not measure a crash
between receiving an answer and saving its event.

## Alternatives and limits

A hand-rolled claim lock in Postgres would need its own claim expiry, retry, timer and recovery rules.
That alternative was not built: not measured.
No speed or correctness advantage over it has been measured.
Temporal adds a service to operate; the choice keeps recovery and review waits in one saved history.

The local Temporal development server stores state on a volume.
Production failover, server data loss and outages beyond five activity attempts are not measured.
The tests do not establish availability for an indefinite storage outage.
[ADR 002](002-review-workflow-patching.md) explains preserving old command histories across deployments.
