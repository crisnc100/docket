You are grading PR2 (phase P3) of Docket v0.1 in this repo (branch v01, diff 942ba4c..HEAD). Read builds/v01/plan.md (Definitions, P3, and the 2026-11-09 simulation decision at the end) and the changed files. Note: evaluations ran on SIMULATED model answers (export/import path) by decision; the live OpenRouter run is deferred and is not a criterion here. Grade strictly; PASS or FAIL per criterion with file/line or behaviour evidence; flag real bugs outside the criteria. Return exactly the JSON specified, no prose.

Criteria:
- D4: choose output schema restricted to shortlist ids + none; no injection document auto-filed to its injected id (tests and held-out eval); explicit id alone never strong; empty shortlist makes zero model calls.
- D5: every document across the held-out eval, including poison cases, ends terminal with a non-empty reason; reconcile converts orphans (test).
- D6: dedup through HTTP: 3 concurrent submits, resubmit after terminal, different message_id -> one document and <=1 filing.
- D7: fault suite kills the worker at real failpoints (after storage write before ledger write; during choose), asserts the failpoint fired, and asserts exactly one object, one filed_documents row, one filed event after restart; API-crash ingress cases covered.
- D8: review approve/dismiss/timeout/double approve(409)/approve after expiry(409)/unknown record id all tested.
- D9: observe mode writes no objects outside raw/ and no act filings; review ends would_review without waiting.
- D10: held-out (seed 11) floors met: wrong-file 0, clean auto-file >= 90%, injection-to-injected-id 0, recall >= rules-only baseline; report has >=8-point curve, baseline comparison, per-case-type table, category accuracy, intervals, simulated banner with simulator provenance; replay eval reproduces in verify.sh.
- D11: shadow-diff report classifies every disagreement against gold.
- SIM: export/import validates each imported answer against its request schema and records provenance per answer; reports never present simulated answers as live measurements.
- WF: workflow determinism replay test against recorded histories exists and passes; bounded retries with non-retryable error classes per plan; workflow input carries a storage key, never document bytes.

Output contract:
{"family":"fable","model":"claude-fable-5-1","criteria":[{"id":"<criterion id>","criterion":"<exact criterion text>","verdict":"PASS","evidence":"<evidence>"}],"findings":[{"likelihood":5,"impact":5,"protected":false,"trigger":"<trigger>","issue":"<issue>","file":"<path>","line":"<line>","evidence":"<evidence>"}]}
findings optional; likelihood/impact integers 1-10; verdict exactly PASS or FAIL.
