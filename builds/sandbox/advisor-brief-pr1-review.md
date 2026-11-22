# Advisor brief: two design calls from Docket sandbox PR 1's review

## THE ASK
Cristian wants a public "try your own document" sandbox that is completely safe but easy: no sign-up, no codes. PR 1 (branch `sandbox`, worktree /Users/cortega/Documents/Projects/docket/docket-sandbox, tip ad8123d) gives each browser a private sandbox (httpOnly cookie, 50-minute life, last 5 minutes refuse new documents so a send then starts a NEW sandbox and replaces the cookie), "Delete now" (DELETE /sandbox) and a sweep. The PR review raised two groups of findings that need a design decision, not a patch.

## WHAT WE KNOW (from the review, reports under reviews/2026-11-22-00-5*/code-review.md)
Group A, rollover and the single cookie:
- A send in a sandbox's last 5 minutes makes a new sandbox and overwrites the only cookie. The old sandbox (documents, dismiss reasons) stays live until expiry, unreachable by Delete now, but the page says "Everything this browser sent is deleted." (docket/sandbox.py:97-140, docket/sweep.py:470-479, web/src/main.ts:91)
- A stale tab still showing the old sandbox deletes the replacement (Delete now sends no sandbox identifier). (web/src/main.ts:129)
- Two first sends from two tabs at once each make a sandbox; last Set-Cookie wins, the other is orphaned. (docket/api.py:420)
- A sample racing Delete now can land in a fresh sandbox and survive; if the DELETE response lands last it clears the new cookie too. (docket/api.py:391, web/src/main.ts:127)
Group B, does Delete now tell the truth:
- Temporal's DeleteWorkflowExecution only enqueues deletion for a closed workflow; Docket treats the ack as done, so "deleted: true" can return while the history is still readable. (docket/sweep.py:191)
- SeaweedFS's master returns at once when another vacuum holds its lock, so a skipped vacuum looks like success and the PDF bytes stay in the volume. (docket/sweep.py:291)
- `_close` commits the sandbox row's deletion before external stores are cleaned; a retry with the same cookie then answers deleted:true while data waits for the sweep. (docket/sweep.py:477)
- The sweep finishes leftovers within minutes; the S7 marker test passes only after a sweep.

## CURRENT DIRECTION (attack this)
A: Replace rollover with a refusal: in the last 5 minutes a send is refused ("This sandbox ends in N minutes; Delete now and start fresh"), so a browser only ever has one sandbox and one cookie. Delete now sends the displayed sandbox's id; the server deletes only if it matches the cookie's sandbox, otherwise 409 and the page reloads. Concurrent first sends: serialise by having the page disable Send until the first response returns, and accept the cross-tab race as a known limit (both sandboxes still expire in 50 minutes).
B: Delete now answers 200 "deleted" only when Postgres rows and S3 objects are gone and Temporal delete was accepted; the copy says "Deleted. Copies in the workflow store and on disk are erased within a few minutes." Keep the sandbox row as a tombstone (closed, not deleted) until the sweep confirms external stores clean, so a retry answers 202 "still erasing" not 200. Accept async Temporal and vacuum as the platform limit, and say so.

## DECISIONS IN PLAY
1. Rollover: refuse in the last 5 minutes, or keep rollover with a cookie that carries every sandbox the browser owns?
2. Delete now's wording and status: is "Deleted; the last copies are erased within a few minutes" honest enough, with a tombstone so retries never say 200 early?
3. Anything in A or B that is still unsafe for real documents from strangers?

## READ FIRST
- /Users/cortega/Documents/Projects/docket/docket-sandbox/builds/sandbox/plan.md (rev 4; PR 1 and Deletion sections)
- /Users/cortega/Documents/Projects/docket/docket-sandbox/docket/sandbox.py
- /Users/cortega/Documents/Projects/docket/docket-sandbox/docket/sweep.py
- /Users/cortega/Documents/Projects/docket/docket-sandbox/docket/api.py (send_sample, delete_sandbox)
- /Users/cortega/Documents/Projects/docket/docket-sandbox/web/src/main.ts
