## 1. SCOPE VERDICT

**Right-sized** on both decisions (refuse instead of rollover; say plainly that the last erase is asynchronous), but each half carries one over-built mechanism to cut: the id-matched 409 on Delete now, and the tombstone held "until the sweep confirms".

## 2. ADVISORY

I ran no tests; this is from reading the code, the plan and ADR 010.

**A. Rollover and the cookie**

1. **Refuse; do not build a multi-sandbox cookie.** A cookie that lists sandboxes would turn `request_owner`/`owned_document` (`docket/sandbox.py:164-180`), the queue filter (`docket/api.py:270-278`) and every row of the S2 test into multi-owner logic, to save one click in the last 5 minutes. One browser, one sandbox is the invariant the copy relies on.

2. **Make the rule "a request that carries a cookie never creates a sandbox".** This is stronger than the direction and replaces two of its mechanisms.
   - Today `write_target` falls through to creation whenever the cookie's sandbox is not writable (`docket/sandbox.py:113-140`).
   - Change it so creation happens only with no cookie. A cookie whose sandbox is in its last 5 minutes gets the refusal; a cookie whose sandbox is closed or gone gets 409 plus the spent cookie ("That sandbox ended. Send again.").
   - This closes the sample-racing-Delete case on the server: the racing send carries the old cookie and is refused, so there is no new cookie for the late DELETE response to clear.
   - Cost: one extra click in the rare case of a cookie that outlives its row.

3. **Cut the "Delete now sends the sandbox id, 409 on mismatch" piece.**
   - With one sandbox per browser, a stale tab's Delete now deletes the browser's current sandbox. That is what the button says, and over-deleting is the safe failure for a privacy control.
   - The 409 adds a path where the user pressed Delete and nothing was deleted, which contradicts "Delete now always works" (plan.md:148).
   - It also means exposing the sandbox id to the page for no other use.

4. **Do not "accept the cross-tab race"; close it with the Web Locks API.**
   - The in-tab half of the direction already exists: `sendSample` disables Send (`web/src/main.ts:480`).
   - What is missing: Delete now stays enabled during a send (`web/src/main.ts:126-141`), and two tabs are not serialised.
   - Wrap both send and delete in one `navigator.locks.request('docket-sandbox', …)`, falling through where the API is absent. That removes the two-first-sends orphan and the cross-tab send/delete race for real browsers.
   - In PR 1 an orphan holds only a synthetic sample. In PR 2 it would hold a stranger's PDF for 50 minutes, out of reach of Delete now, so treat this as required before uploads are turned on.

5. **Change the copy at `web/src/main.ts:91`** from "Everything this browser sent is deleted." to a claim about this sandbox. A scripted client or a browser without Web Locks can still orphan a sandbox, so the browser-wide sentence can never be guaranteed.

6. **Missing: the refusal collides with PR 3 as planned.**
   - "Delete now" also clears the visitor's units from the tab (plan.md:131, 371-372), so "Delete now and start fresh" at minute 45 makes them retype their units.
   - That is the hostility bounce item 19 was answered with automatic rollover (plan.md:721). R1 ("the page never asks for Delete now") and R6 ("units survive a rollover") would both fail.
   - Fix: make the refusal's action a separate one-click "Start fresh" that deletes the server sandbox, keeps the tab's units and re-sends. In PR 1 it can be the same DELETE call.
   - The plan needs a rev 5 for line 41, R1, R6 and item 19. `ROLLOVER` (`docket/sandbox.py:23-25`) stays as the refusal window.

**B. Does Delete now tell the truth**

7. **The direction on wording is right: one sentence that is always true, and accept the platform limit.**
   - Do not poll Temporal to earn a stronger claim. The vacuum skip cannot be detected from the master's reply (ADR 010:57 records that two concurrent vacuums both answer 200), so a "verified" 200 would still be unverified for disk bytes.
   - "Deleted. The last copies in the workflow store and on disk are erased within a few minutes." is honest enough.

8. **Cut the tombstone-until-confirmed.**
   - Once 200 means "nothing is reachable; erasure finishes within minutes", a retry that answers 200 is true, so the problem it solves is gone.
   - A retry with the same cookie barely exists anyway: every DELETE response spends the cookie (`docket/api.py:441`).
   - The no-cookie request already answers `deleted: true` by design (`docket/sweep.py:474-478`), which a tombstone cannot change.
   - It would cost a new row state the Postgres pass must skip (`docket/sweep.py:126-143`) and coupling between passes that the plan forbids (plan.md:141-143).
   - Item 2 covers the race it would also have closed.

9. **Missing: "within a few minutes" is false while the sweep is stale.** `delete_now` never consults `sweep_stale`, and the sweep is the only backstop for a skipped vacuum or a queued Temporal delete. When the sweep is stale, answer 202 with copy that makes no time promise, such as "Deleted from Docket's database; erasing the last copies is delayed."

10. **Correct the "at once" claims and the acceptance checks.**
    - Reword `README.md:73`, `docs/operations.md:142` and `:258`, and `docs/adr/010-sandboxes.md:13`.
    - Reword S5 (plan.md:205) to: rows and objects are gone when it answers; histories and disk bytes are gone after the next complete sweep.
    - Have S7 assert the bound explicitly (marker gone within one sweep interval), not pass incidentally after a sweep.
    - Add to ADR 010 that deleting a closed workflow is only queued. Its 11 ms measurement (ADR 010:40) covers a running workflow only.

**Decision 3: anything still unsafe for strangers' documents?** With items 2, 4, 5 and 9, I found nothing else. A write racing Delete is covered by the lock (`docket/sandbox.py:183-204`), expired sandboxes are invisible at once (`docket/sandbox.py:75-85`), and the row-first order in `_close` is safe because the sweep finds leftovers by listing the stores.

## 3. QUESTIONS

1. **Automatic rollover was the plan's answer to "expiry is hostile" (item 19). Is a refusal with a one-click "Start fresh" (keeps units in PR 3) an acceptable replacement?** Recommended: yes. It costs one click in the last 5 minutes and buys the one-sandbox invariant.

2. **Should a stale tab's Delete now delete the browser's current sandbox, or refuse with 409 and reload?** Recommended: delete. Losing re-sendable documents is a better failure than a Delete button that did not delete.

3. **Should the cross-tab lock ship in PR 1, or wait as a gate on turning uploads on?** Recommended: PR 1. It is about ten lines, and it also fixes the same-tab send/delete race that exists today.
