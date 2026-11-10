## slice 2026-11-10-02-42-36-79d93e f1 (medium) docket/core/candidates.py:105
Address matching accepts a different unit as corroboration. Normalization turns `Apt 1-B` into `unit 1 b`, which contains the configured `unit 1` address. With shipped records and thresholds, `UNIT-0001\nAddress: 1010 Cedar Street Apt 1-B` produces address=1, conflict=false and decision=file at .6000000. A mismatching address therefore bypasses the single-signal safeguard.
Trigger: shortlist(load_records(), text='UNIT-0001\nAddress: 1010 Cedar Street Apt 1-B'), followed by score_choice for UNIT-0001 with the default scoring config.
Evidence: 

## slice 2026-11-10-02-42-36-79d93e f2 (medium) docket/core/candidates.py:125
Building evidence is marked explicit even when it comes entirely from the matched address. An accepted Record with building='Cedar' and address='1010 Cedar Street Apt 1' yields independent address and building signals from that address alone. With valid strong/auto-file thresholds of .35, it scores .4000000 and returns file, contrary to the required exclusion of address-derived building evidence.
Trigger: Configure building='Cedar' for a Cedar Street record and valid strong/auto-file thresholds of .35; shortlist and score a document containing only that record's address.
Evidence: 

## slice 2026-11-10-02-44-21-9431cf f1 (medium) docket/llm/gateway.py:122
MEDIUM 15/100: SDK output failures bypass the non-retryable ParseError contract. The actual OpenRouterTransport raises LengthFinishReasonError or ContentFilterFinishReasonError before returning a result, and this handler rethrows them unchanged. Reproduced both with HTTP responses carrying the corresponding finish_reason. These failures skip local validation and usage settlement, leaving the worst-case reservation charged and exposing a retryable exception.
Trigger: A live or record call reaches its output-token limit or receives finish_reason='content_filter'.
Evidence: 

## slice 2026-11-10-02-44-21-9431cf f2 (medium) docket/corpus.py:133
MEDIUM 15/100: Case-specific record selection can crash on valid configuration despite eligible records being available. Record permits an empty former_tenants list and buildings containing one unit, but generate selects these records without checking eligibility. With seed 7, clearing UNIT-0094's former tenants raises IndexError after four PDFs are written; assigning UNIT-0055 its own building raises IndexError after six writes. Both configurations pass Record validation, leaving generation incomplete and potentially mixing new PDFs with an old manifest.
Trigger: Regenerate seed 7 after a valid records.csv update leaves UNIT-0094 without former tenants or makes UNIT-0055 the only unit in its building.
Evidence: 

## slice 2026-11-10-02-41-36-f58c4a f1 (medium) config/records.csv:198
records.csv gives two different units the same tenant name, and the candidate ladder turns that into a forced conflict. "Brian Young" is the current tenant of UNIT-0197 and a former tenant of UNIT-0129. Any document for UNIT-0197 that names its tenant makes shortlist() add UNIT-0129 to corroborated_ids (docket/core/candidates.py:121, exact former-name match), so conflict is true and score_choice returns review with score 0. Three committed corpus documents are labelled expected=filed but take this path: seed-11 doc-0035 (clean), seed-11 doc-0045 (exact_duplicate) and seed-7 doc-0068 (very_long). On held-out seed 11 that is 1 of 10 clean cases, which puts the clean auto-file rate at exactly the 90% floor before any other miss. "Joseph Jackson" is duplicated the same way as a former tenant of UNIT-0089 and UNIT-0198. Traced by reading candidates.py, scoring.py, corpus.py and the labels; not executed (no run tool in this session).
Trigger: uv run docket score corpus/seed-11/doc-0035-clean.pdf (label says filed; the trace gives conflict=true, would_auto_file=false). The same happens for any UNIT-0197 document containing "Tenant: Brian Young", and in every seed-11 eval run.
Evidence: 

## slice 2026-11-10-02-41-36-f58c4a s1 (high) compose.yaml:27
Temporal's unauthenticated dev server is exposed to the network. Docker publishes these ports on every host interface, allowing an attacker who can reach the developer's machine to invoke Temporal APIs and create persistent executions, consuming its disk and service resources even before Docket workers exist. This crosses the intended local-development boundary. See [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/) and [Temporal authorization defaults](https://docs.temporal.io/references/service-configuration).
Trigger: Run docker compose up -d --wait on a host reachable from an untrusted network; an attacker connects to host port 7233 without credentials and submits Temporal API requests.
Evidence: 

