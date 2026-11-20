Read builds/beta/plan.md in this repo. For background, read builds/v02/plan.md's "PR4 — Hosted beta" section, which rev 3 revises, and the code it touches: docket/review_access.py, docket/api.py, docket/settings.py, docket/llm/budget.py, docket/llm/gateway.py, docket/corpus.py, compose.yaml and web/login.html.

You are an adversarial reviewer with no attachment to this plan. Attack it:
- What is missing?
- What will break?
- Which edge cases and failure modes did the author miss?
- Where is the acceptance bar too weak to catch a bad build?
Pay special attention to:
- anything a stranger on the internet could use to spend money, write data, or get past the invite gate;
- whether the three-PR split leaves main working after each merge.
Be concrete, and cite file:line. Do not rewrite the plan. List the gaps, and say why each matters, ranked most serious first.
