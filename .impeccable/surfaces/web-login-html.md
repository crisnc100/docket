---
version: 1
slug: "web-login-html"
primary_target: "web/login.html"
related_targets: ["web/src/main.ts","web/src/style.css"]
---

# Reviewer page

Scope: the reviewer page served at `/reviewer`: the token form `web/login.html` and the review app `web/src/main.ts` with `web/src/style.css`. Mode: Operate.

Audience: property-operations staff who settle held documents every day, on a desk screen and sometimes a phone.
Job: settle one held document quickly and never file it to the wrong unit.
Information, in reading order: the document itself; why it waits, in plain words; the candidate units with their clues and scores; the rules' top pick; the model's suggestion, labelled as a suggestion that never files; the actions (approve the rules' pick, approve the suggestion, approve another candidate, dismiss with a reason).
States: token form (empty, checking, wrong token, load failure); queue with items, empty queue, no document selected; a held document; a document being saved; a filed or dismissed document; preview unavailable; request errors in the status line.
Constraints: behaviour, routes, API calls, the token flow, labels, roles and test ids unchanged. The page CSP allows only nonce or blob: styles and scripts and same-origin fonts, so fonts and tokens live in the inline nonce style of `login.html` and the app sheet uses them by name. Nothing scrolls sideways at 390px.

Chosen: the established Plain Standard world from DESIGN.md, extended to this surface. No new identity work; the brief asked for code-led building.

## Direction contract

THESIS: The reviewer page is the front page's demo panel made real: one document on the desk, its evidence beside it, and the decision at the end, in the same ink, hairlines and two outcome colours. It refuses the admin-dashboard look of cards, sidebars with icons, badges and coloured buttons.

OWN-WORLD: White ground, ink text, 1px hairlines; Held Amber only on the held status and the "why it waits" box, Filed Green only on a filed status; Focus Blue only on focus rings and selection. Geist for language, Geist Mono for file names, unit ids, scores and the trace. 8px radius only on the document panel, the token panel and buttons; one panel lift on the document panel (or the token panel on the form page).

STORY: The reviewer opens the oldest held document, sees the PDF, reads in one sentence why Docket held it, compares the candidates' clues and scores, sees the rules' pick next to the model's suggestion (marked as never filing), and approves, picks another, or dismisses.

FIRST VIEWPORT: A 64px top bar with the wordmark and "Document review". Below, a 20rem queue on the left as hairline rows (file name in mono, sender beneath, the open one on Soft Grey), and on the right the raised document panel: file name and status on top, then the PDF on the left half and the evidence column on the right half, ending in the actions. Under 1100px the panel's two halves stack; under 760px the queue sits above the panel.

FORM: canon (the established world), played straight; extension of an existing surface system, no concept roll. seed: none (unavailable)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
