You are the implementer for Docket v0.1, phase P1 only. Work only inside /Users/cortega/Documents/Projects/docket/docket-v01 (git worktree, branch v01).

Objective: P1 "Foundations and corpus" from builds/v01/plan.md is complete and its exit checks pass.
Read first: builds/v01/plan.md (whole file — Definitions and P1 are binding; later phases are context only), builds/v01/context.md.
Steps: plan.md P1.1–P1.6, in order. For each step run ./verify.sh (create it in P1.1) and the step's tests; read failures, fix, re-run.
Constraints:
- Only the stack listed in plan.md "Stack (fixed)". Any other dependency: stop and call back.
- Do not start P2+ work. No scope creep; no extra features, adapters or UI.
- P1.4 model spike needs a live OpenRouter key. If OPENROUTER_API_KEY is not set in the environment or .env, implement the spike script (`uv run docket spike-models`), skip running it, and list it as PENDING in your checkpoint — do not fail the phase on it, and do not fabricate results.
- Never commit secrets; only .env.example. Commit at phase end (one or more commits, clear messages, no attribution trailers) including the builds/ folder and a builds/v01/log.md recording deliberate choices.
- Never push, open a PR, or run reviews, grades or other models.
- Do not delete, skip, weaken or narrow tests or checks to make something pass. Zero skipped tests is a rule.
Validate: ./verify.sh (lint, pyright, pytest zero-skips, the P1 tests) plus `docker compose up -d` healthchecks for postgres, temporal, minio.
Stop when: every P1 exit check passes (spike may be PENDING as above), OR a choice is not determined by the plan — then stop and state the choice with your recommended answer.
Write your checkpoint as: what was built, each P1 exit check with PASS/FAIL and the command output that proves it, deviations, open questions with recommended answers.
