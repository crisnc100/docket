# Docket v0.1 — context

## Product
A document intake and filing engine. A document arrives by webhook or CLI. Docket works out which real record it belongs to, files it to storage and records every step. Nothing is dropped silently. Uncertain matches go to a review queue instead of being guessed.

The portfolio claims to prove:
1. Exactly-once filing on top of at-least-once delivery, shown by fault-injection tests.
2. The LLM can only choose among real candidate records, with deterministic corroboration.
3. A measured precision vs. auto-file-rate trade-off.

## Demo domain
Property management: about 200 units, with leases, inspection reports, invoices and notices, all generated as synthetic PDFs.

## Environment (checked 2026-11-09)
- uv 0.9.11. The project pins Python 3.12 via uv.
- Docker 29 with the daemon up. Node 23 with npm. The Temporal CLI is not installed, so the Temporal dev server runs in Docker Compose (`temporalio/temporal` image, `server start-dev`).
- gh is not logged in, so pushing waits on Cristian.
- The repo is a fresh git repo at `~/Documents/Projects/docket`. The worktree is `docket-v01` on branch `v01`, and there is no remote.

## Constraints
- LLM access goes through OpenRouter, via LangChain `ChatOpenAI` with `base_url`. Cheap models, with the model id in config.
- Budget: $50/month total across both projects. Every live run has a hard per-run budget, and CI never calls a live model.
- Replay mode means tests, CI and demos run with no key.
- The repo is public from day one, so commit no secrets.
- Synthetic data only.

## Out of scope for v0.1
OCR for scanned documents, Docling, Qdrant candidate search, learning from review corrections, Gmail/Graph connectors, Dropbox/Drive storage, auth, deployment, multi-tenancy.
