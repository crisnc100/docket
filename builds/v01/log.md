# Docket v0.1 build log

## P1 checkpoint — 2026-11-09

P1.1 is complete locally. P1.2 is interrupted by a stack/toolchain decision. P1.3–P1.6 have not started. No P2+ implementation, live model call, review, push or PR was performed.

### Deliberate choices

- Python is pinned to 3.12. The global managed 3.12 interpreter failed with `Bad CPU type in executable (os error 86)`. A native interpreter and uv cache live under ignored `.cache/` inside this worktree; verify.sh selects those paths.
- uv_build is the uv project's packaging backend, not an application dependency. All direct runtime and check dependencies are from the fixed stack; uv.lock pins their resolutions.
- The Typer CLI currently exposes only `version`. No later-phase command is scaffolded.
- Pytest rejects skip/skipif markers during collection and fails the session on runtime skips. CI runs the same verify.sh, with a Postgres 16 service. The test suite exercises Temporal's real time-skipping test environment without a live model.
- Secret scanning includes untracked files. Generated caches and raw harness logs are excluded; known public local-development credentials have explicit allowlist annotations. No live credentials are included.
- Compose contains only the P1 infrastructure services. API and worker belong to later phases.
- Draft SQLAlchemy models and Alembic environment are present but no migration has been generated or applied; these are not claimed complete.

### Decision blocking P1.2

The fixed stack requires MinIO. The initial official Docker Hub image pull failed:

```text
$ docker compose up -d --wait
Image minio/minio:RELEASE.2025-09-07T16-13-09Z Error pull access denied for minio/minio, repository does not exist or may require 'docker login'
```

The alternative official registry also failed:

```text
$ docker pull quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z
unexpected status from HEAD request to https://quay.io/v2/minio/minio/manifests/RELEASE.2025-09-07T16-13-09Z: 401 UNAUTHORIZED
```

The official repository documents source-only distribution and a Go build requirement:
https://github.com/minio/minio#source-only-distribution

**Recommended answer:** authorize a pinned official MinIO source build in a Docker build stage, including the Go build toolchain. Keep MinIO as the runtime service. Do not substitute an S3 implementation or use an unvetted third-party image. This is a callback under the user's fixed-stack constraint; no source build or additional toolchain has been added.

### Exit checks and evidence

| P1 exit check | Result | Evidence |
| --- | --- | --- |
| `./verify.sh` on current implementation | PASS | Output below; this proves the current scaffold, not completion of unimplemented P1 tests. |
| Postgres `pg_isready` | PASS | `/var/run/postgresql:5432 - accepting connections` |
| Temporal Compose cluster health | FAIL | Server exited before healthcheck; log below. |
| MinIO `/minio/health/live` | FAIL | Image cannot be pulled; service never started. |
| Alembic schema and constraints | FAIL — not completed | No version migration yet; blocked during P1.2. |
| Gateway replay tests | FAIL — not implemented | P1.3 not reached. |
| Gateway record-refusal tests | FAIL — not implemented | P1.3 not reached. |
| Budget pre-check tests | FAIL — not implemented | P1.3 not reached. |
| Persisted daily-cap tests | FAIL — not implemented | P1.3 not reached. |
| Model spike and ADR | FAIL — not implemented | P1.4 not reached. Key availability has not been inspected; the no-key exception has not been claimed. |
| Records and categories config | FAIL — not implemented | P1.5 not reached. |
| Corpus seeds 7/11, case coverage and determinism | FAIL — not implemented | P1.6 not reached. |

```text
$ ./verify.sh
Resolved 88 packages in 4ms
Audited 85 packages in 7ms
All checks passed!
15 files already formatted
0 errors, 0 warnings, 0 informations
collected 2 items
tests/test_scaffold.py .
tests/test_temporal_environment.py .
2 passed in 0.32s
detect-secrets: {}
```

```text
$ docker compose up -d postgres temporal --wait
Image temporalio/temporal:1.5.1 Pulled
Image postgres:16 Pulled
Container docket-v01-temporal-1 Started
Container docket-v01-postgres-1 Started
container docket-v01-temporal-1 exited (1)

$ docker compose logs --no-color --tail 20 temporal
temporal-1 | time=2026-11-09T20:09:13.138 level=ERROR msg="failed starting server: failed setting up schema: unable to create SQLite admin DB: unable to open database file: out of memory (14)"
```

Temporal's volume/database startup failure remains undiagnosed. The time-skipping test environment passed independently; it does not prove Compose cluster health.

### Deviations and next action

- Stopped during P1.2 under the explicit callback condition. No tests were skipped or weakened.
- P1 is incomplete; no phase-end commit has been made. The existing plan/context/bounce files and harness-owned phase1 brief/logs were preserved.
- Resolve the MinIO build-toolchain choice, then finish P1.2 (including Temporal startup, migration and database tests), run verify.sh, and continue P1.3–P1.6 in order.

Worktree: `/Users/cortega/Documents/Projects/docket/docket-v01`; branch: `v01`; starting HEAD: `c97253a`; configured application URL: none yet. Infrastructure ports: Postgres 5432, Temporal 7233/8233, MinIO 9000/9001.

## P1 resumed — 2026-11-09

The operator rejected a MinIO source build and authorized SeaweedFS S3 instead.
The incomplete checkpoint above is historical; the final state and command
evidence are in `builds/v01/p1-checkpoint.md`.

Deliberate choices:

- SeaweedFS is pinned to official `chrislusf/seaweedfs:4.53`. Its single-process
  `mini` mode persists both objects and filer metadata under the named `/data`
  volume. Only the S3 port is published, on loopback at 9000. The healthcheck
  uses the master's `/healthz`; a separate boto3 `list_buckets` call confirms
  that the S3 endpoint accepts authenticated requests. No storage adapter was
  implemented in P1, and no application code depends on SeaweedFS-specific APIs.
- Temporal keeps its default uid 1000. A one-shot container using the same image
  creates and assigns `/data/server` on the named volume to that uid before the
  server starts. The SQLite DB is `/data/server/temporal.db`. Cluster health also
  passed after restarting Temporal and SeaweedFS.
- Local Postgres uses loopback port 55432 because a host Postgres already listens
  on 5432. CI uses its isolated Postgres service on 5432 via DATABASE_URL. Tests
  migrate an isolated schema per database fixture and drop only that schema.
- The initial Alembic migration owns the five planned tables and their indexes,
  foreign keys and dedup/event constraints. Review state remains in documents.
  Status transitions, the filing ledger's behavior and S3 storage belong to P2.
- OpenRouter is configured through ChatOpenAI with a strict per-call schema,
  only the configured provider, no provider fallback and require_parameters.
  Local Pydantic validation also checks the enum. The fallback parses raw JSON
  from the same paid response; it does not issue another request.
- Replay identities include activity, prompt version, configured model and the
  normalized digest of inputs, prompt and schema. Normalization canonicalizes
  JSON key order, Unicode NFC and newlines. JSONL appends use file locks, fsync
  and conflict detection. Record mode requires source `synthetic_corpus` before
  any budget or transport work, and recordings contain results, not raw inputs.
- One RunBudget instance must be shared across a run's gateway instances. It is
  process-local; the daily global cap is persisted and coordinated by Postgres.
  Daily rows temporarily include reservations. A conditional UPDATE prevents
  concurrent workers from exceeding the cap. Settling refunds the difference
  between the worst-case reservation and OpenRouter's actual usage cost against
  the original UTC day. A crash or ambiguous network failure retains its worst
  reservation for the day; this deliberately sacrifices availability for a safe
  spend limit. Explicit 429s refund the reservation and back off at 1/2/4/8 seconds,
  with at most five attempts; SDK retries are disabled.
- The default token price ceilings are $0.30/M input and $1.20/M output, enforced
  in OpenRouter's provider routing. Worst-case reservation bounds input tokens
  by serialized UTF-8 bytes plus 4096 framing tokens and uses the explicit output
  token limit. Missing/invalid usage cost retains the reservation and raises a
  typed error. No price estimate is presented as a measured cost.
- No OpenRouter key is set. The live enum spike is PENDING, with a command and
  prediction/protocol ADR committed. No result JSON or live model measurement
  was fabricated. Tests of the request wire format use local HTTP responses.
- The config contains 200 synthetic units in ten buildings. Tenant/former-tenant
  fields and address aliases are lists encoded with `|` in CSV. Pydantic validates
  the exact five categories, schema version, signals and safe record components.
  Category signal requirements are conservative initial config, not thresholds
  tuned against held-out seed 11. No candidate or scoring rules were implemented.
- Each seed has ten examples of all 17 case types (170 files). Gold for conflicts,
  building-wide, unknown and poison cases is none; injection gold remains a real
  unit and the adversarial id is a different real unit. Ambiguous fixtures expect
  review. Corrupt/encrypted/zero-byte/non-PDF fixtures use the corpus's coarse
  expected=rejected label; this is not a workflow terminal-status assertion.
- PDFs use reportlab invariant=1. Encryption fixtures use a public test password
  and RC4 solely to exercise password protection without an additional crypto
  dependency. Determinism compares labels and extracted text (decrypting the
  encrypted fixture with that password); encrypted bytes need not reproduce.
  Exact duplicates separately require byte identity with their clean source.
- Corpus generation uses local seeded Random and Faker instances. Seed 7 is for
  later tuning; seed 11 remains held out. Corpus labels express the desired gold
  outcome and do not claim that an unbuilt filing engine already achieves it.
- Tests use the ignored worktree-local cache for temporary regeneration artifacts.
  Intentional public local credentials and the synthetic PDF password are
  annotated for detect-secrets; live keys are never written. Harness-owned raw
  phase logs are ignored; build plans, briefs, checkpoints and this log are kept.

Validation progression: P1.1: 2 passed; P1.2: 8 passed; P1.3: 34 passed;
P1.4: 36 passed plus live spike PENDING; P1.5: 44 passed; P1.6: 54 passed (including the spike measurement/ADR writer tested locally).
All step verify.sh checks ended green after correcting the observed failures.
No skipped tests, added external runtime dependency, P2+ implementation, review,
grade, other-model call, push or PR was performed.


Phase-end commit attempt: `git add` failed creating the shared worktree
`index.lock` with `Operation not permitted`. The shared Git directory is outside
the sandbox's writable worktree. No staging, commit, push or workaround occurred.
The source and build artifacts remain intact for the orchestrator to commit from
a session with the required Git metadata write access.

## P2 work — scoring policy pending

The operator accepted P1 and committed it as ac20871. Commit attempts are now
prohibited for this seat; all P2 changes remain in the worktree.

Implemented typed extraction, top-five candidate matching/conflict detection,
generic conditional S3 storage, Postgres event/status ledger and branch coverage
enforcement. The scoring policy lacks concrete weights, thresholds and defined
independence; asked for that choice while completing independent modules. No
scoring implementation or invented measured result was added.

Deliberate choices:
- A malformed PDF is a typed, non-retryable parse error, not a new rejection
  reason outside the fixed whitelist. Empty text layers remain typed extraction
  results for later review routing. LLM truncation removes exactly the middle.
- Literal IDs are bounded to avoid partial-ID matches; address normalization uses
  full unit token boundaries and config aliases. ID matches outrank exact address,
  then tenant match, then explicit building. Fuzzy tenant cutoff defaults to .88
  and is a caller parameter. Ties break by record id, independent of config order.
- Exact address/current/former-name evidence can expose distinct record ids even
  without literal IDs in the text. Conflict detection examines all evidence before
  truncating to five candidates. Shared building-only matches do not create false
  record-level conflicts.
- The storage adapter accepts a typed S3 client protocol, exercised with boto3;
  this avoids extra SDK typing dependencies or strict-pyright suppressions. No
  SeaweedFS-specific request is used by application code. Conditional PUT prevents
  overwrite; a retry hashes the stored bytes rather than trusting ETag or metadata.
- SeaweedFS autoCreateBucket is disabled after a real negative test exposed its
  nonstandard default. The test was retained. Test-created temporary resources
  from the failed negative test were cleaned up; no user buckets were touched.
- The ledger uses caller-owned transactions so status and status-event writes can
  roll back atomically. Transitions lock and refresh the row; duplicates are no-ops
  and preserve first-write details/reasons. Every terminal state's outbound edges
  are empty. The only nonterminal states are received, processing and needs_review.
- The coverage checker enforces branch counts separately from line/combined
  percentages, fails closed on missing modules, and is itself tested. Candidate,
  ledger and storage branch coverage are all 100%; scoring is absent and correctly
  makes verify.sh fail. Full tests: 149 passed, zero skips. Core-only: 95 passed.

Detailed exit status, command output and the policy recommendation are recorded in
builds/v01/p2-checkpoint.md. No P3+ behavior, other models, reviews, grades, pushes,
PRs, existing-test edits, dependency additions or commit attempts occurred.

## P2 final — complete, uncommitted

The operator resolved the scoring policy. The earlier pending checkpoint and .70
proposal above are historical; builds/v01/p2-checkpoint.md records the final state.

All scoring numbers are **untuned defaults**, explicitly labelled by
`calibration: untuned_defaults` in config/scoring.json: ID .30, address .30,
tenant .25, building .10, filename .05; medium threshold .25, strong threshold .55,
auto-file threshold .55. Seed-7 tuning happens in P3. No tuning was performed
against seed 7 or held-out seed 11 in this phase.

The hard independent-signal gate is separate from the weighted total. Body/subject
ID repetition does not create more evidence. A matching filename ID can be separate
evidence, while a disagreement sets conflict and forces review. A building must
have explicit provenance; an address-derived building contributes neither another
weight nor another independent signal. Zero-weight signals do not satisfy the gate.

A one-signal effective score is capped one database score quantum (0.0000001) below
strong, so even a configured dominant ID/filename weight cannot bypass the gate.
Scoring retains the raw weighted total for diagnosis. Conflicts override the
usable score to zero and always return review. Decimal arithmetic truncated to
seven fractional places matches the existing score column and avoids floating
point boundary errors in the approved .55 examples.

The requested score table passes: ID+address .60/strong; ID+tenant .55/strong;
address+tenant .55/strong; ID alone and repeated 3x .30/medium; building+filename
.15/none; injection ID plus gold address conflict/review. Additional tests prove
single-signal safety with dominant weights, explicit versus derived building,
matching versus conflicting filename IDs, thresholds, malformed inputs and config.

Final `docker compose up -d --wait`: Postgres, Temporal and SeaweedFS healthy.
Final `./verify.sh`: ruff/format clean, strict pyright 0 errors, Alembic succeeds,
192 passed in 20.52s with zero skips, and detect-secrets {}. Branch-only floors:
candidates 20/20 (100%), scoring 30/30 (100%), ledger 16/16 (100%), storage 20/20
(100%). All 54 P1 tests remain unchanged and pass. Scoring-only: 43 passed,
30/30 branches covered. No further code changes were made after the passing run.

P2 has no open implementation question. No P3 work, tuning, additional dependency,
model call, review, grade, push, PR or commit attempt occurred. All changes remain
in the v01 worktree for the operator to commit, per instruction.

## PR1 grader fix round — complete, uncommitted

Read builds/v01/grade-pr1.json and addressed f1–f5 plus optional f7. f6 remains
blocked on Cristian's OpenRouter key; no live model or new review/grade was run.

Added a dedicated regression module before production edits. It demonstrated 13
failures across every fixed finding, with 6 already-correct delimiter cases
passing. The original set then passed 19/19 after the fixes. Seven additional CLI
cases exercise rejection output, empty/filename-only review and missing files.

The ID pattern accepts non-alphanumeric filename separators while retaining
alphanumeric boundaries. Single-signal address/tenant golds now expect review;
filename-only and former-tenant-only already did. Both seeds were regenerated.
Random encrypted byte changes were discarded only after verifying identical
old/new decrypted page text, keeping the data diff limited to labels.

Auto-file config must meet or exceed strong_threshold, and the file decision
explicitly requires Tier.STRONG. Weights and .55 strong/auto-file defaults remain
untuned and unchanged. No independence guard or clean-case floor was weakened.

The rules-only score CLI extracts full text, builds candidates using the original
basename, scores each, sorts by effective score with stable ties, and prints JSON
including signals, tiers and would_auto_file. It has no LLM or filing effects.
Malformed PDFs raise a typed DocumentRejection subclass with corrupt reason and
rejected status; the ledger accepts corrupt and the CLI reports that rejection.
SeaweedFS health now checks its S3 gateway at 8333/healthz, verified live.

Existing-test updates are deliberate behavior changes: the invalid ID suffix case
now uses an alphanumeric continuation instead of the newly legal hyphen; the
maximum-score conflict test now uses the minimum valid threshold instead of a
forbidden .01. Their original safety assertions remain, with new regressions
covering the corrected cases and config validation. No skipped/deleted tests or
weakened check/coverage gate was introduced.

Final ./verify.sh: 218 passed in 20.96s, zero skips, strict pyright 0 errors,
ruff/format clean, detect-secrets {}. Branch-only module coverage remains 100%:
candidates 20/20, scoring 32/32, ledger 16/16, storage 20/20. Existing seed
regeneration/determinism tests pass. CLI clean corpus example chooses UNIT-0083
at .9500000/strong with would_auto_file=true. No code changed after this green run.

Finding-by-finding file:line and test evidence is in
builds/v01/pr1-fix-checkpoint.md. Changes stay in the worktree for the operator;
no commit attempt, push, PR, P3 implementation or model call occurred.

## P3 partial checkpoint — evaluation policy unresolved

Step 0 restores the bypass-validation conflict regression as a new sibling;
minimum-valid-threshold coverage remains. The separate commit paths and 219-test
verification are recorded in step0-done.md. No commit was attempted.

P3 uses the same storage-key workflow input for ingress and recovery. The received
event persists its non-secret workflow context so reconcile can start an ingress
interrupted after the DB insert. Shortlisting precedes model activities: an empty
shortlist bypasses both paid calls. Review signals preserve the requested workflow
interface; an update endpoint supplies an atomic acceptance/rejection result for
HTTP 409s. Observe routes finalize immediately without non-raw storage effects.
No webhook, frontend or P4-only command is included.

Time-skipping tests use scripted activities. Integration tests use actual core,
Postgres and SeaweedFS with explicit scripted model fixtures; none of these
responses are recorded as model measurements. A genuine captured Temporal history
is stored as YAML with unchanged protobuf JSON values, allowing field-specific
annotations of inspected synthetic base64 payloads and the worker checksum. No
secret scanner exclusion or check was weakened.

Implementation stopped at a scope conflict: the new uncommitted decision at
plan.md:177 asks for export/import and Claude simulation, while the task specifies
no-key evaluation PENDING and forbids other models. The operator was asked which
policy applies. Fault-process tests, complete eval gates, reports and deployment
checks remain unfinished. See p3-checkpoint.md for measured checks and open work.

At this stop, ./verify.sh exits 0: 242 passed in 30.29s, zero skips, all required
module branches 100%, pyright 0 errors, ruff clean, detect-secrets {}. This is
verification of the current partial implementation, not P3 exit acceptance.

## P3 — implemented, awaiting operator simulation

The operator resolved the earlier evaluation choice: implement export/import and
never invoke a model; the operator supplies all simulation answers. Gateway
export serializes the same prompt/schema helpers used by actual activities,
including the per-call shortlist enum. Import validates request digests, answer
schema and target provenance before replay admission. Conflicting replay rows
remain immutable. Simulated cost is null and completed simulated reports begin
with an explicit simulator/target banner. No operational answer was authored.

The importer uses only the JSON Schema subset emitted by product models and
fails closed on unsupported constraints. No dependency or lockfile changed.
Shortlisting precedes both model activities so empty shortlists produce no calls
or exports. Scoring numbers remain untuned defaults; seed-7 tuning and held-out
quality checks require operator answers. No model quality number was invented.

The real process-fault suite confirmed exit 137 and the exact fired log for all
five tests. API crashes cover both ingress gaps; received context supports
reconciliation. Worker restarts recover both after S3-before-ledger and during
choose. Three concurrent HTTP submits plus a terminal resubmit preserve one row,
one filing object, one filing row and one filed event, while retaining four
sources. Stopping actual SeaweedFS produces EndpointConnectionError; retry after
recovery still gives one filing. Test-only model fixtures do not populate the
provider archive or contribute evaluation numbers.

Captured actual Temporal test histories cover happy filing and an opened review
approved by update. Replayer passes on both. ADR-002 describes patch guards and
retaining old command branches, and labels a future deploy prediction unmeasured.
ADR-004 and docs/rerecord.md describe operator simulation versus real-provider
measurements. Minimal HTTP endpoints exist solely for P3 dedup/review/fault checks;
P4 webhook, UI, preview and remaining commands are absent.

MacOS Bash requires a nonempty pytest argument array under set -u, so each verify
mode explicitly collects tests. Normal mode excludes only the new process-fault
directory; fault mode includes it and all ordinary tests. No existing test file
changed against 942ba4c, and no assertion, zero-skip rule or branch floor was
weakened. Generic non-retryable activity failures get the correct reason rather
than a misleading exhaustion reason; tests prove a single attempt/finalization.

The raw secret scanner still scans all files with its existing exclusions. Known
synthetic history payloads/checksums and public request-key lines have narrow
annotations. Future JSONL digest findings are recognized only when they equal a
verified exported request hash. A real-scanner integration test proves a different
secret on that same row still blocks verification. No whole-row or whole-archive
exception was added.

Final ./verify.sh: 274 passed in 48.30s, zero skips. Final ./verify.sh --fault:
279 passed in 172.05s, zero skips. Ruff/format pass, pyright 0 errors, and required
core branch coverage is 100% (candidates 20/20, scoring 32/32, ledger 16/16,
storage 20/20). The explicitly pending held-out replay report reproduces with no
model calls; detect-secrets {}. Docker builds all three app images. Final compose
up --wait exits 0: Postgres, Temporal, SeaweedFS, API and worker healthy; actual
boto3 head_bucket and API /healthz return HTTP 200.

Final export commands for seeds 7 and 11 exit 0. Each seed has 200 unique pending
files: 100 classify and 100 choose. There are 400 total, with no overlap, zero
operator answers and no replay JSONL archive. Both 170-document reports contain
eight-point rules-only curves and 17 case types; model metrics, floors and actual
shadow disagreements remain PENDING-SIMULATION. See p3-checkpoint.md for each exit
check, command evidence, deliberate choices and the operator callback. No commit,
push, PR, model invocation, review or grade was attempted.

## P3 simulation results

The operator supplied all 400 answers.
All passed their saved response schemas and were imported.
Saved provenance confirms 382 Haiku answers and 18 Opus answers.
Each seed has 191 Haiku answers and 9 Opus answers.
All unique saved answers were used.
The operator reports false safety blocks on 18 synthetic filler text requests.
Those requests were rerun on Opus. The archive keeps each model identity.
The operator says each call saw only its own prompt, messages and schema.
It saw no expected labels, code or other answers.
The implementer did not make a model call or edit an answer.

Both seed imports and both replay evaluations exit 0.
Seed 7 passes all filing floors with 29 correct filings.
Seed 11 passes all filing floors with 28 correct filings.
Seed 11 has zero wrong files, 90% clean filing and zero filings to injected IDs.
Recall is 93.33%, equal to the rules-only baseline.
Category accuracy is 87.69%, versus 93.85% for the baseline.
The difference comes from no_record cases assigned misc without a model call.
No score threshold, label or answer changed to obtain these results.

Every seed has 170 terminal results with a reason.
Observe mode has zero filing rows and no non-raw objects in the default bucket.
The shadow reports classify 82 differences on seed 7 and 87 on seed 11.
Both sides route those documents to review as expected.
Their proposed records and categories can still differ.

Reports start with the simulated banner.
They show the supplied mix, the used mix and the operator's fallback reason.
Counts come from saved answers. Provider cost stays null.
The replay gate checks each document, metrics, curve and model provenance.
New tests check the counts, context source, banner and unknown cost.
No existing test changed in this round.

Final verify: 276 passed in 51.06s.
Final verify --fault: 281 passed in 165.34s.
Both have zero skips and 100% required branch coverage.
Ruff, pyright, Docker builds and secret checks pass.
Both commands reproduce seed 11 with no model calls.
All five compose services report healthy after the fault run.
The real-provider checks remain deferred under the approved simulation decision.
All P3 checks pass under that decision. Nothing was committed or pushed.
See p3-checkpoint.md for the curve, each exit check and command output.

## Graded corpus and scores

The shared generation rule is in graded-corpus-rule.md.
It is saved before any new held-out evaluation.
The operator owns its commit. No commit was attempted.
Each seed has ten appended graded_evidence documents and 180 documents total.
A separate random stream preserves the original generator sequence.
The original 340 payloads and label prefixes match saved content hashes.
Regeneration produces new encryption bytes for poison PDFs.
The worktree keeps their original bytes.

A signal strength measures evidence quality from zero to one.
Names use the larger of direct and word-sorted RapidFuzz similarity.
Addresses use normalized RapidFuzz similarity with exact numeric components.
A missing or different unit number gives no fuzzy address support.
Exact names, normalized exact addresses and exact ids retain strength one.
Grading follows candidate selection to preserve request keys.
Candidate admission, order, conflicts and the strong-tier gate are unchanged.
Weights, tier thresholds and fuzzy cutoffs remain untuned defaults.

The threshold script reads only seed 7 and makes no model call.
It chooses 0.5857142 using the rule saved before selection.
That rule maximizes correct filings with zero wrong filings and a 90% clean floor.
It breaks ties with the largest threshold.
The seed-7 rules-only curve has six distinct points across eight thresholds.
Precision stays one whenever there is a filing. Higher thresholds only lose coverage here.
This sample shows no measured precision gain from raising the threshold.

The original 400 request keys, request files, answers and recordings are unchanged.
Only 40 requests were exported: 20 per seed, ten for each model activity.
The graded-seed manifests list only those new keys.
No answer or recording was written for them.
The reports describe the pending 180-document runs.
They do not reuse measurements from the older 170-document runs.
Those earlier reports remain in the accepted base commit.
Scoring waits for the new answers. Seed 11 has not selected any settings.

Response labels describe the sources actually used.
Zero responses gives no model calls. Provider plus simulated responses gives mixed.
Two simulators still give simulated, with each simulator named in provenance.
Reports and shadow reports preserve these labels. Unknown model cost stays null.
The model-choice comparison and new held-out floors await the operator's answers.

Verification passes: 302 tests normally and 307 with the real fault suite.
Both runs have zero skips and 100% required branch coverage.
The replay check reproduces the saved pending state, not a new measured report.
See graded-export-checkpoint.md for command evidence and each existing-test edit.

## Graded corpus measurements

The operator committed the shared rule as 85669b2 before the held-out measurement.
The scoring config remains unchanged at threshold 0.5857142.
The operator supplied 40 more independent Haiku answers.
All pass schema and provenance checks. The import adds 20 recordings per seed.
The original 400 recording bytes are unchanged.
The saved mix is 422 Haiku answers and 18 Opus answers.

Both replay evaluations pass. Seed 7 files 38 of 180 documents.
Seed 11 files 36 of 180, with zero wrong filings and 90% clean filing.
Its recall is 36/39, equal to the rules-only baseline.
No injection document is filed to its injected id.
Each seed's eight-point curve has six distinct precision/rate points.
Raising the threshold loses filings here, with no measured precision gain.
The held-out graded score 0.5842105 remains below the selected threshold.
That result did not change any setting.

The model's choose step changes no filing/review decision or filed record on either seed.
Review proposals differ on 74 documents in seed 7 and 79 in seed 11.
They still go to review. No filing gain from model choice is measured.
Category accuracy is 124/140 on both seeds, versus the baseline's 132/140.
Eight empty-shortlist no_record documents per seed receive misc without a model call.
The reports and README state these limits.
Both shadow reports classify every disagreement as both_correct against gold.

A new table tests the unchanged shipped config with two independent signals.
A score of 0.55 is strong but must go to review below 0.5857142.
A score of 0.60 must file. No existing test changed.
New report tests keep filing, review-proposal and category differences separate.
The replay check includes the choice comparison.

Verification passes: 313 tests normally and 318 with the real fault suite.
Both have zero skips and 100% required branch coverage.
Both reproduce the measured seed-11 report. All compose services are healthy.
No model call, commit or push was made by the implementer.
See graded-import-checkpoint.md and graded-import-evidence.txt for the evidence.

## PR2 conflict and address identity checks

Full-strength name and address matches are checked across all records before selection.
Full strength is 1.0, including names with reversed word order.
An explicit id and a different record's full match force review.
Matches outside the selected candidates still contribute to the conflict flag.
Selection priority and scoring thresholds remain unchanged.
Address lines are normalized once per document for the shared record scan.

House and unit identifiers retain letters and hyphens.
Unit 1A does not match unit 1. Unit 1-2 matches neither unit 1 nor unit 12.
The abbreviation rules for Street/St and Apt/# remain.
The supplied two-tenant example has raw score 0.65 but must go to review.
The conflict flag caps its score at zero.
The wrong-unit examples supply no address support and remain ID-only review cases.

New tests use the shipped records and scoring config.
Before the code change, nine conflict cases and four address cases failed.
Three abbreviation controls passed. All sixteen pass with the change.
The explicit-id conflict cases also require raw scores above the filing threshold.
No existing test changed.

All 440 replay requests are unchanged. No export or new answer was needed.
Both replay evaluations pass with the same per-document results and metrics.
No seed-11 floor changed: zero wrong filings, 90% clean filing,
zero filings to injected ids and recall 36/39, equal to the baseline.
The threshold stays at 0.5857142. No setting was selected using seed 11.
All corpus, config, request, answer and recording files remain unchanged.
No model call or commit was made by the implementer.

Verification for the conflict and address checks passes with 329 tests normally
and 334 tests with faults. Both have zero skips and 100% required branch coverage.
Both reproduce the measured seed-11 report. All five compose services are healthy.
See x1-x2-checkpoint.md and x1-x2-evidence.txt for failing-first and replay evidence.

## Address words and unit identifiers

Apt, Apartment, Unit and # identify a unit.
The whole unit identifier keeps letters and internal hyphens.
Other hyphens separate address words, including those in filenames.
The fuzzy address guard compares letter-leading units as well as numeric ones.
A1 and B1 supply no address support for each other.
Unit 1-2 remains distinct from unit 1 and unit 12.
The conflict rule, two-signal rule and filing thresholds remain unchanged.

The new regression file has 23 cases. Before the implementation change,
20 failed and three controls passed. All 23 pass with the change.
No existing test changed. Direct checks against f497aaf reproduce both findings.
Both supplied triggers go to review with the worktree code.

Both seed evaluations pass with all 440 requests unchanged.
Every report field, both shadow reports and all saved inputs remain unchanged.
Seed 11 still has zero wrong filings, 90% clean filing, no filings to injected ids,
and recall 36/39, equal to the baseline.
The reports remain simulated measurements. No external model was called.

Verification passes with 352 tests normally and 357 tests with faults.
Both have zero skips, 100% required branch coverage and a reproduced held-out report.
All five compose services are healthy. Secret checks pass.
No dependency, commit or review was added by the implementer.
See address-token-checkpoint.md and address-token-evidence.txt for command evidence.

## PR1 review fixes on core

Unit identifiers retain letters and internal hyphens.
Hyphens between address words remain separators, including in filenames.
An address phrase is removed before a building mention is counted as independent.
Separate building mentions still count. Candidate ranking and scoring policy are unchanged.

Client output and schema exceptions become non-retryable ParseError failures.
Valid reported usage settles the reservation. Unknown billing keeps the reservation.
Timeout handling, rate-limit retries and recording rules remain unchanged.

Corpus cases select records with the evidence they need.
Generation stops before writing if no record has former tenants or no building has two units.
The shipped config keeps its existing selection sequence.
Tenant names belong to only one unit. The name helper keeps first owners and uses seed zero
for replacements, avoiding every existing name. Only two name fields changed.
Both seeds were regenerated. Three PDFs changed; labels and filenames are unchanged.
Existing encrypted PDFs were retained where regenerated decrypted text was identical.

Every published compose port binds to 127.0.0.1.
Container inspection confirms all four bindings and healthy backing services.

The regression tests reproduced 22 failures before implementation, with seven passing controls.
All 30 new tests pass. No existing test changed.
Full verification passes with 249 tests and zero skips.
Candidate branch coverage is 96.15%; scoring, ledger and storage are 100%.
Both corpus determinism tests and secret checks pass.
No model call, review, grade or commit was run by the implementer.
See pr1-review-fix-checkpoint.md and pr1-review-fix-evidence.txt for evidence.
## Replay after the accepted tenant corrections

The operator supplied four replacement answers: three Haiku and one Opus.
All pass request-schema and provenance validation.
The original 440 recording bytes remain unchanged; four entries were appended.
The archive has 425 Haiku and 19 Opus answers, including four old document versions.
Current seed requests use 423 Haiku and 17 Opus answers.
Report banners name the counts for each seed and separate them from the archive.
The extra Opus answer follows a false Haiku safety block reported by the operator.

Only three accepted core PDF hashes changed in the original payload guard.
Their bytes were checked against core. The prior hash map is preserved in corpus/history/.
Seed-7 trial counts were revalidated after the tenant correction.
The original selection is preserved in evals/history/. The threshold stays at 0.5857142.
No scoring config changed and no setting was selected using seed 11.
The existing payload and trial-table assertions remain intact.
New tests check the three permitted data changes and the unchanged threshold.

Both seed evaluations pass. Seed 7 files 39 documents; seed 11 files 38.
The held-out floors pass: zero wrong filings, 100% clean filing,
zero filings to injected ids and recall 38/39, equal to the baseline.
Choose changes no filing decision or filed record on either seed.
Review proposals still differ on 74 and 79 documents.
Category accuracy remains 124/140 versus the baseline's 132/140.
Both curves have eight points and six distinct precision/rate pairs.
The regenerated reports and shadow diffs remain labelled simulated.

The incoming PR1 port test expected only the three backing services and four bindings.
Its expectations include the P3 API and fifth binding. Its all-port loopback assertion is unchanged.
This is the only existing test file changed and needs the operator's separate test-only commit.
Normal verification passes with 412 tests; fault verification passes with 417 tests.
Both have zero skips, required coverage, reproduced replay results and clean secret checks.
All five services are healthy. No model call, review, grade, commit or push was run.
See reimport-four-checkpoint.md and reimport-four-evidence.txt for evidence.
