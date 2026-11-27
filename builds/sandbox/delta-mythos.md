# Security review of the `units-v1` delta (plan rev 3): needs changes

The injection guarantee holds in code, and the $1 cap cannot be blown. The plan still needs changes: its "worst case" cost figure is wrong by about 13×, no test would catch one sandbox's units going out with another's document, and the copy overstates deletion of the browser's copy of the units.

I checked against the code on `sandbox` and recomputed the plan's numbers with the repo's prompt builders. PyPI and privacy.claude.com are blocked here, so the cost formulas ran as standard-library copies and the two Anthropic quotes are unverified.

## Your five questions

- **Pick outside the shortlist: no.** The answer must validate against an enum of the shortlisted ids plus `none` (`docket/activities_schema.py:19-26`) on both output paths (`docket/llm/gateway.py:280`, `:370`). I ran the schema: it rejects `UNIT-9003`, `UNIT-0042`, `" UNIT-9001"`, `unit-9001`, a list, and an extra key.
- **Automatic filing: no.** The rules' top score decides and the model's pick is stored only as a suggestion (`docket/activities.py:184-190`). The file step refuses without strong evidence or a manual approval (`:245-250`).
- **Another sandbox's data: not by design, but untested.** See item 2.
- **Exfiltration through the suggestion: no.** Only `prompt`, `schema` and `system_prompt` reach the transport (`docket/llm/gateway.py:221`). What comes back is one of at most six values, or a fixed failure reason (`docket/llm/errors.py:33`), shown to the visitor who supplied every input.
- **Cost cap: holds.** Every attempt reserves against the day's ledger before sending (`docket/llm/gateway.py:213-217`, `docket/llm/budget.py:140-147`), and output is capped at 256 tokens with thinking off (`docket/llm/anthropic.py:142-144`). The plan's reservation figure is wrong, though; see item 1.

## Findings

### Must-fix

None.

### Should-fix

1. **The $0.0164 "worst case" is not a worst case, and R11 cannot notice.**
   - The plan assumes 4 bytes per character. The reservation is priced on the canonical JSON (`docket/llm/replay.py:24-27`), which escapes control characters to 6 bytes and NFC-expands some characters to 12 (U+1D160 becomes three 4-byte characters).
   - Past 100,000 bytes the long-prompt price applies, five times higher (`config/model_prices.yaml:13-15`, `docket/llm/budget.py:49-57`).

   | Text (8,000 characters) | classify reserve | `units-v1` reserve (5 units) |
   | --- | --- | --- |
   | Four-byte characters (the plan's case) | $0.0070 | $0.0082 |
   | U+0001 | $0.0509 | $0.0569 |
   | U+1D160 | $0.0989 | $0.1049 (units too: $0.1161) |

   - One document can therefore reserve about $0.215, against a gate of $0.0164.
   - **Why it matters:** the gate only compares headroom with the ceiling and reserves nothing (`docket/beta_samples.py:142-150`). Uploads it admits then fail `budget_exceeded` inside classify. About ten such calls in flight hold the whole $1 for seconds, failing samples and other visitors' documents.
   - **Encoding is unspecified:** the plan says only `json.dumps`. With the default `ensure_ascii=True`, 10 astral-character units reserve $0.0608, not $0.0094.
   - **Fix:** for uploads, strip control characters and NFC-normalise the text before the 8,000 cut (`docket/core/extraction.py:44`); normalise unit fields at `PUT` and count length afterwards; state `ensure_ascii=False`.
   - **R11 should become:** for adversarial texts and units through the real builders, the gateway's reservation never exceeds the gate's ceiling. The existing ceiling test only checks a range (`tests/test_beta_samples.py:329-334`).

2. **No test fails if sandbox B's request carries sandbox A's units.**
   - Every sandbox has `UNIT-9001` and starts at the same revision, so a lookup or cache keyed by revision, id or content hash sends A's tenant names with B's document. The answer is still a valid id, so nothing breaks.
   - R5 tests only the routes; R4's capture test uses one sandbox.
   - **Why it matters:** this is the one path by which the delta could leak third parties' names.
   - **Add:** two sandboxes with the same ids and revision, different marker text, documents processed concurrently. Each captured request must contain its own markers and none of the other's. Also require choose to fail closed if a shortlisted id is missing from the loaded units object.

3. **R4 and R9 test the builder, not what is sent, and R4 skips failure paths.**
   - R9 is "prompt-builder tests", but the bytes on the wire come from `AnthropicTransport.body` (`docket/llm/anthropic.py:128-149`). In the fallback mode the schema moves into the system prompt (`:135-139`).
   - Capture the full body in both output modes. Assert exactly the shortlisted units, no marker from a non-shortlisted unit, and the system prompt as written.
   - Exception text does reach Temporal and the worker log. If units are loaded through `Record.model_validate` (`docket/config.py:57`), a validation error quotes the input, which is the unit text. The current code already chains errors this way (`docket/activities.py:69-72`).
   - **Add to R4:** a corrupt or missing units object, with the marker absent from Temporal and logs. Raise fixed messages with no chained cause.

4. **R10 should name the output path and include both output modes.**
   - "Scripted transport" must mean a `Reply` going through `read_reply` (`docket/llm/gateway.py:358-372`), not a stubbed gateway; otherwise a build that skips validation passes.
   - Include `{"record_id":"UNIT-9001","x":1}`, a list, a refusal and a cut answer.
   - Include an in-list answer that differs from the rules' top candidate on a document the rules file: the filed record must stay the rules' choice.

5. **The copy overstates deletion for the browser's copy of the units.**
   - "What Docket keeps" says "your units go with them" within an hour, and the page says "Private to this browser · deleted in N minutes". The units stay in the browser until "Delete now".
   - The plan does not say which storage; `localStorage` would keep tenant names indefinitely on a shared computer. The page today uses only `sessionStorage` (`web/src/main.ts:57`).
   - **Fix:** specify `sessionStorage` or an expiry, and say in both places that the browser copy stays until the tab closes or "Delete now".

### Notes

6. **Privacy sources unverified.** I could not reach articles 7996866 and 7996868, so the two quotes and their dates need a human re-read before the copy ships. The rest of the copy matches the code: text goes out only when a candidate exists (`docket/workflows.py:128`), at most 8,000 characters (`docket/core/extraction.py:44`), and only shortlisted units, at most 5 (`docket/core/candidates.py:288-298`). The units notice says "they are sent", which errs toward over-disclosure.

7. **"Delete now" and calls not yet sent.** The plan documents a call "already sent". A choose call waiting in backoff (`docket/llm/gateway.py:37`, `:255`) can still send text and units for a few seconds afterwards. Say "a call in progress" in `docs/operations.md`.

8. **"The workflow checks it again" is looser than stated.** `docket/workflows.py:76` checks `valid_ids`, which is every record (`docket/activities.py:104`), not the document's candidates. Only the API check binds to candidates (`docket/api.py:328-330`). The plan should also say the shortlist activity sets `valid_ids` to the visitor's ids.

9. **The suggestion still steers a person.** A hostile PDF can push the model toward any shortlisted unit, and the page offers "Approve suggested" (`web/src/main.ts:301`). The effect is bounded to the visitor's own shortlisted units, and no filed copy is written.

10. **Predictable ids.** Any document containing "UNIT-9001" gets an explicit-id clue for every visitor's first unit (`docket/core/candidates.py:313`, `:371`). Filing still needs a second clue (`docket/core/scoring.py:16`, `:138-140`), so this is not exploitable without knowing the visitor's units.

11. **The reservation can be slightly under the bytes sent.** The bound is computed on NFC-normalised text, but the raw prompt is sent. For 8,000 Hangul jamo that is 23,994 bytes sent against a 20,531 bound. The overshoot is recorded and capped at one call (`docket/llm/budget.py:173-178`); item 1's normalisation removes it.

12. **Request logging.** Nothing in the repo logs prompts, and the one log line that quotes the API carries only a schema error (`docket/llm/anthropic.py:70`). Consider a startup refusal for `ANTHROPIC_LOG` in beta, since the SDK's debug logging prints request bodies and S7 would not see it.

13. **Confirmed as the plan states.**
    - The `v1` pin recomputes to `0cde2e5d…120648`.
    - Today's sample ceiling is $0.0147, and the plan's case gives $0.0082 per choose and $0.0163 per document.
    - Record mode refuses uploads (`docket/llm/gateway.py:175`), and recordings are written only for the synthetic corpus (`:283`, `:327`).
    - Unit text containing `"}]`, newlines and "UNTRUSTED DOCUMENT:" reads back exactly and cannot forge the delimiter line.
    - Bounce rows 3, 6, 10, 13 and 18 are answered, apart from items 1 to 5 above.

**Verdict: needs changes.**
