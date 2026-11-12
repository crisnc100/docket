# Docket v0.2 build log

## PR1: Anthropic transport, recording format, price snapshot, predictions

Branch `real-model`, from the plan commit `ac2ba1e`. No live call was made and no money
was spent. Every transport test runs against a fake HTTP layer (`httpx2.MockTransport`
under the SDK). An autouse fixture blanks both API keys for every test.

### What was built

- `docket/llm/anthropic.py`: AsyncAnthropic with SDK retries off and a 30 s timeout.
  The request carries the system prompt, one user turn, a strict schema (refs inlined,
  objects closed), thinking off, effort low, and no sampling settings. A 400 that names
  the schema switches the process to JSON in the prompt, logged once.
- Gateway: each reply is charged from usage times the snapshot before parsing. There
  are four unreadable reasons: refusal, cut, not_json and schema. Each is recorded and
  raises `UnreadableAnswer` live and in replay. `NoAnswer` means never billed, so the
  request may be resent. `MaybeBilled` means charged at its worst case, never resent.
- Live recording format in `evals/recordings/anthropic.jsonl`. Simulated lines keep
  their v0.1 shape.
- `config/model_prices.yaml` and `load_prices`. `PriceCeiling.cost` and `worst_case`
  both apply the long-prompt tier.
- Settings: `DOCKET_MODEL_PROVIDER`, `DOCKET_MODEL`, `ANTHROPIC_API_KEY` and
  `DOCKET_RUN_BUDGET_CAP_USD`. When `DOCKET_REPLAY_PATH` is unset, it follows the
  provider.
- Eval: the live banner, a refusal to mix live answers with others, and the key and
  cap checks. `reproduce_eval.py` replays the stored report under that report's model.
- `docket spike-models` with Anthropic: two calls on a $0.05 budget. It writes into
  ADR 008's Spike section.
- The live exchange file: `pack_exchange.py --live`, plus `--check` for the live
  archive. The secret check covers the live archive.
- ADR 008 with predictions P1–P8, committed before any result.

### Choices the plan did not settle

The orchestrator accepted all of these as recommended.

1. The replay path default follows the provider. The simulated replay is pinned to
   `openai/gpt-4o-mini` on OpenRouter in tests and in `reproduce_eval.py`.
2. `DOCKET_RUN_BUDGET_CAP_USD` (default 5) enforces the hard cap in code. The run and
   daily defaults are unchanged.
3. `NoAnswer` and `MaybeBilled` are typed errors with their own workflow failure
   reasons. No-answer requests are not written to the archive.
4. `UnreadableAnswer` keeps the Temporal type `ParseError`, so workflow handling is
   unchanged.
5. Record mode writes each live request to `evals/pending`. `pack_exchange.py --live`
   packs the requests.
6. When a pass spans two UTC dates, the banner names both.
7. SDK 2.8.0 sends the key as `authorization: Bearer`, not `x-api-key`. The D22 test
   checks that header.

### Existing tests changed (test-only commits)

- `8e93345`: five tests pinned to the simulated target model and provider.
- `c851b7d`: import order.
- `53c6bd0`: the browser and spike tests pinned to OpenRouter, and the autouse
  key-blanking fixture.
- `225b9ef`: the new Anthropic gateway tests write their exchange under `tmp_path`.

None of them changes an assertion.

### Verify

`./verify.sh` passes in full on `025513c`, the PR1 code. Its results: 681 tests with
zero skips, the coverage gates, the pack check, "Replay report reproduced: PASS; seed=11;
no model calls", and detect-secrets clean. The secret scan skips the root `.env`, which
holds the local key, and a step before it fails unless `git check-ignore` confirms that
`.env` is ignored.

One earlier run had a timing failure in `tests/core/test_structural_matching_bounds.py`.
Two timings were 0.011 s and 0.028 s, a ratio over 2.5, while the browser stack was
running. The file then passed alone three times, and the full rerun passed. PR1 does not
change `docket/core`.

### What PR2 needs

- `.env` with `ANTHROPIC_API_KEY` and `DOCKET_DAILY_BUDGET_USD=5`. Defaults: provider
  anthropic, model claude-haiku-5-5, `DOCKET_REPLAY_PATH` unset.
- `uv run docket spike-models`. Then `docket eval --seed 7 --mode record --budget 2.00`
  and the same for seed 11.
- `uv run python scripts/pack_exchange.py --live` before `./verify.sh`. `--check` fails
  once live recordings exist without their archive.
- The report's new measures: unreadable answers by reason and activity, no-answer
  count, output mode, spend by activity, three costs per document, and latency. The
  recordings hold every field these need. No-answers show as workflow reason
  `no_answer`.
- Point `reproduce_eval.py` at the live report. It already reads the model from the
  stored report.
- Until PR2 lands live recordings, a compose stack on the new defaults has no answers
  to replay. Tests pin the simulated model.
