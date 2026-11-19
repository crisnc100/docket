# Advisory brief: rules' pick note when scores are zeroed

## THE ASK
Docket redesign PR2 restyles the reviewer page (/reviewer). It is a look-only PR: no backend changes. Cristian's copy rule: plain, deliberate lines, and no claim the data on screen contradicts.

## WHAT WE KNOW
- The review fix round (commit ad99136) made the rules' pick card note conditional. On conflict it reads "The rules list this unit first. Compare the candidates with the document." Otherwise it reads "The highest-scoring unit." (web/src/main.ts, around lines 264-270).
- Conflict comes from hasConflict(): the shortlist trace's `conflict` flag, falling back to the hold reason (main.ts around line 124).
- The builder's sibling check found that scores can also be zeroed without a conflict: when the matching budget is exceeded (reason input_too_large), or when category_unreadable supersedes that reason. Every candidate then shows score 0, yet the note says "highest-scoring".
- The shortlist event has no over_budget flag. The score event carries rules_reason only when classification failed. See docket/activities.py around lines 100-130 and the scoring code under docket/.

## CURRENT DIRECTION
The builder proposes a new rule: show the neutral "listed first" note whenever every candidate score on screen is 0, not only on conflict.

## DECISIONS IN PLAY
Pick one:
- (a) Build the all-zero rule.
- (b) A simpler fix with no new rule, for example always using the neutral "listed first" wording and dropping "highest-scoring".
- (c) Leave the code as is and file this as a deferred finding.
If a simpler fix than these will do, say which.

## READ FIRST
- /Users/cortega/Documents/Projects/docket/docket-redesign/web/src/main.ts
- /Users/cortega/Documents/Projects/docket/docket-redesign/docket/activities.py
- `git -C /Users/cortega/Documents/Projects/docket/docket-redesign show ad99136`
