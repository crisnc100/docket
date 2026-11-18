---
version: 1
slug: "web-public-home-index-html"
primary_target: "web/public/home/index.html"
related_targets: []
---

# Front page

Scope: the public front page, `web/public/home/index.html` with its CSS, JS and `demo.json`. Mode: Persuade.

Audience: hiring managers and business owners judging whether this is trustworthy applied AI; property-ops staff curious about the review page.
Job: understand in one viewport how a document gets filed or held, then try it on real test documents.
Action: pick a test document and watch it run. Secondary: read the held-out results, open the source.
Proof: recorded seed-11 results only (`evals/reports/2026-11-13-seed-11.json`), checked by a test.
Constraints: plain language, research terms behind a fold, no gradients or glow, nothing decorative, synthetic data stated, two missed gates stated with a link. A quiet labelled slot for "try your own PDF" (later, hosted beta), not built.

Chosen: the standing exit, Plain Standard, picked by Cristian on 2026-11-18. Craft bar, chosen by the builder because no one could be asked: Plausible's and Tailscale's public pages (calm, exact, honest numbers).

## Direction contract

THESIS: A plain product page whose craft is exactness: the demo is the hero, and every figure is the real one. It refuses the AI-startup split hero with a glowing screenshot, gradient text and logo row.

OWN-WORLD: White ground, near-black ink, one deep green for "filed", one amber-brown for "waits for a person", and one blue kept for focus rings, clue highlights and text selection; hairline 1px rules, 8px radii only on the demo panel and buttons, and one soft ink-tinted shadow on the demo panel alone; Geist for text and fixed settings, Geist Mono only for data the pipeline produced (unit ids, scores, extracted text).

STORY: The visitor learns that Docket files a document only when two clues agree, watches three real test documents get decided (one filed, two held), sees that the model suggests but never decides and was less accurate than the rules, and leaves knowing the data is synthetic and two gates were missed.

FIRST VIEWPORT: Left column (about 40%): one-sentence headline, two-sentence lede, primary button "Try a test document" (scrolls focus to the demo), secondary link to the held-out results, then three short fact lines with real numbers. Right column (about 60%): the demo panel at full height: document tabs on top, the document's text (what Docket reads) beside a four-step track: read, find units, score, decide, ending in the outcome.

FORM: canon (standing exit), played straight; not on the ordered list. seed: seed:ef1491d345c19843

Signature interaction: choosing a document replays the four steps in order, each step revealing its real data (extracted text, candidate units with clue chips, weighted score against 0.586, decision with who decided); reduced motion shows the final state at once.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
