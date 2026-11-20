# Advisory brief: two new rules in Docket beta PR C's fix round

## THE ASK
Code review 2026-11-20-16-32-27-cadd8a of PR C (builds/beta/plan.md, PR C section) raised two MEDIUMs that the builder says need new decision logic. I need a verdict on each design, or a simpler fix that will do.

## f3 — overlapping resets (docket/beta_reset.py:134)
**Finding:** "Overlapping resets can reopen sample submissions before the later reset finishes. The exclusive lock ends when the flag transaction commits. A second reset can then set the same flag and proceed, and the first reset can clear it before the second truncates the tables." It happens when a manual reset (`compose run --rm --no-deps reset docket beta-reset`, from the README runbook) runs during the 08:00 UTC daemon run or the startup catch-up.

**Builder's design:**
- reset() takes a session-level pg_try_advisory_lock on its own key, on one dedicated connection, before anything else, and holds it to the end.
- If the lock is already held, the reset refuses with "A reset is already running." and exits non-zero. The flag is untouched.
- The daemon counts a refusal as a failed attempt, retries in 5 minutes, and finds the reset is no longer due.
- A crashed reset releases the lock when its connection closes.

**Alternative:** wait for the lock (pg_advisory_lock) instead of refusing.

## f5 — run budget lost when a gateway is evicted (docket/runtime.py:97)
**Finding:** "Evicting a gateway discards spend already charged to that document's run budget. A later model activity for the same document gets a fresh budget and can make a billable request that its original budget would refuse. The daily ledger still applies." It was marked protected (wrong money).

**Builder's design:**
- A separate map of RunBudget on the Runtime, keyed like the gateway cache.
- A document's entry is dropped when finalize records a final status, and any entry older than 24 hours is dropped too, because a terminated workflow never runs finalize.
- The gateway LRU stays at 256, and a re-created gateway reuses the document's RunBudget.

**Builder's simpler alternative:**
- Raise the LRU cap to about 10,000, and document that eviction still resets a run budget, with the daily ledger as the real bound.
- The per-document worst case is about $0.015 per attempt, against a $2 run cap that never binds in practice.

**Context:**
- The beta's daily ledger is $1.00, and it is enforced in Postgres at every reservation.
- Local mode has DOCKET_RUN_BUDGET_USD, which a user can set low.
- The gateway cache was unbounded before PR C. PR C added the LRU (plan Build 2) to stop a long-running beta from growing without limit.

## DECISIONS IN PLAY
1. f3: is the try-lock-and-refuse design right, and does it need the dedicated connection? Is there a simpler correct guard?
2. f5: the budget map, the bigger cap, or something else? Is the finding's protected "wrong money" status real, given the daily ledger?

## READ FIRST
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/beta_reset.py
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/runtime.py
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/llm/gateway.py
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/llm/budget.py
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/activities.py
