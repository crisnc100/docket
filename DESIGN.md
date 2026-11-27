---
name: Docket
description: Property documents filed to the right unit, or held for a person.
colors:
  ink: "#16181d"
  ink-2: "#4f5562"
  ink-muted: "#6b717c"
  ink-hover: "#2c3039"
  paper: "#ffffff"
  soft: "#f6f7f9"
  line: "#e2e4e9"
  line-strong: "#c9ccd3"
  filed: "#17614f"
  filed-bg: "#e8f3ef"
  filed-text: "#1f4d42"
  held: "#8a4b00"
  held-bg: "#fbf1e1"
  held-text: "#5f3a08"
  focus: "#2f5bd3"
  clue-mark: "#e7ecfa"
  clue-mark-edge: "#c6d2f4"
typography:
  display:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(2.125rem, 0.9rem + 2.4vw, 2.875rem)"
    fontWeight: 680
    lineHeight: 1.05
    letterSpacing: "-0.032em"
  headline:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(1.625rem, 1.2rem + 1vw, 2.125rem)"
    fontWeight: 650
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  lede:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(1.0625rem, 0.98rem + 0.25vw, 1.1875rem)"
    fontWeight: 400
    lineHeight: 1.5
  title:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "\"tnum\" 1"
  body-prose:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Geist, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
  data:
    fontFamily: "Geist Mono, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.65
rounded:
  standard: "8px"
  full: "50%"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  2xl: "24px"
  gutter: "clamp(16px, 4.4vw, 64px)"
  band: "clamp(56px, 8vh, 88px)"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.standard}"
    padding: "0 18px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.ink-hover}"
  button-quiet:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.standard}"
    padding: "0 18px"
    height: "44px"
  button-quiet-hover:
    backgroundColor: "{colors.soft}"
  button-replay:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.standard}"
    padding: "6px 12px"
  tab:
    textColor: "{colors.ink-2}"
    padding: "12px 10px"
  tab-selected:
    textColor: "{colors.ink}"
  demo-panel:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.standard}"
  document-text:
    backgroundColor: "{colors.soft}"
    textColor: "{colors.ink}"
    typography: "{typography.data}"
    padding: "16px"
  outcome-filed:
    backgroundColor: "{colors.filed-bg}"
    textColor: "{colors.filed}"
    padding: "8px 12px"
  outcome-held:
    backgroundColor: "{colors.held-bg}"
    textColor: "{colors.held}"
    padding: "8px 12px"
  step-marker:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.full}"
    size: "22px"
  step-marker-done:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
---

# Design System: Docket

## Overview

**Creative North Star: "Plain Standard"**

A plain product page whose craft is exactness. White ground, near-black ink, hairline rules, and one working demo that carries the page; every figure on screen is the recorded one. The world refuses the AI-startup look: no split hero with a glowing screenshot, no gradient text, no logo row. Character comes from spacing, a tight Geist ramp, and the honesty of the data, not from ornament.

Colour is spent on meaning. Deep green means "filed", amber-brown means "waits for a person", and those two hues appear only where a decision is shown. Everything else is ink on white, separated by 1px lines. Geist Mono marks what the pipeline read or produced (document text, unit ids, scores), so a visitor can tell machine output from explanation at a glance.

Motion is a replay, not decoration: choosing a document plays the four steps in order, each revealing its real data, and reduced motion shows the final state at once.

This system covers the public front page (`web/public/home/`) and the reviewer page served at `/reviewer` (the token form in `web/login.html` and the review app under `web/src`). The front page is the Persuade surface; the reviewer page is the Operate surface, where the same world shows up as plain, dense, scannable working UI.

**Key Characteristics:**
- White ground, near-black ink, two outcome colours used only for outcomes.
- 1px hairline rules do the structural work that cards and shadows do elsewhere.
- Geist for language, Geist Mono for pipeline data.
- One raised object per page: the demo panel.
- Real numbers, tabular figures, plain words; research terms live behind a fold.

## Colors

A near-monochrome ink-on-white palette with two muted decision hues and one blue reserved for focus and highlights.

### Primary
- **Ledger Ink** (ink): headings, body emphasis, the primary button fill, the selected tab underline, the score meter fill, and a finished step's marker before the outcome is known.

### Secondary
- **Filed Green** (filed, with filed-bg ground and filed-text for supporting lines): only the "filed" outcome box and the step markers once a document has filed.
- **Held Amber** (held, with held-bg ground and held-text for supporting lines): only the "waits for a person" outcome box and the step markers once a document is held.

### Tertiary
- **Focus Blue** (focus): the 2px focus outline on every interactive element. Its pale relatives, **Clue Mark** (clue-mark, edged with clue-mark-edge), highlight the phrases in the document text that produced a clue; text selection uses Clue Mark too.

### Neutral
- **Paper** (paper): the page ground and the demo panel body.
- **Soft Grey** (soft): recessed grounds: the tab strip, the document text block, the demo footer, the meter track, quiet-button hover.
- **Hairline** (line): every divider, table rule, list rule and section border.
- **Strong Hairline** (line-strong): the demo panel border, quiet-button and replay outlines, pending step rings, link underlines at rest.
- **Second Ink** (ink-2): ledes, prose, captions, navigation links, table header cells.
- **Muted Ink** (ink-muted): step titles that have not played yet.
- **Ink Hover** (ink-hover): the primary button's hover fill.

### Named Rules
**The Outcome-Only Colour Rule.** Green and amber appear only where Docket shows a decision. They never decorate headings, links, icons or charts.

**The Waiting-Is-Correct Rule.** Held Amber is a calm, earthy tone, never an alarm red. Holding a document for a person is shown as a safe outcome, not an error.

## Typography

**Display Font:** Geist (with system-ui, -apple-system, Segoe UI, sans-serif)
**Body Font:** Geist (same stack)
**Label/Mono Font:** Geist Mono (with ui-monospace, SF Mono, Menlo, monospace)

**Character:** One neutral grotesque set tight and heavy at the top, quiet and roomy in running text; a monospace of the same family for anything the pipeline produced. Both are self-hosted variable fonts (weights 100 to 900).

### Hierarchy
- **Display** (680, fluid 34 to 46px, line-height 1.05, tracking -0.032em): the single page headline, set as two deliberate lines.
- **Headline** (650, fluid 26 to 34px, line-height 1.15, tracking -0.02em): one per content band.
- **Lede** (400, fluid 17 to 19px, line-height 1.5, Second Ink): the paragraph under the headline, capped at 34em.
- **Body** (400, 16px, line-height 1.55): base text, with tabular figures on everywhere so numbers align. Prose bands step up to 17px in Second Ink with bold phrases in Ledger Ink, capped at 66ch.
- **Title** (600, 15px, tracking -0.01em): step names, outcome headings, buttons, table captions, the fold summary.
- **Label** (400 to 500, 13px, Second Ink): captions, table header cells, meter labels, the demo footer.
- **Data** (Geist Mono 400 to 500, 13px, line-height 1.65): document text, file names, unit ids, scores.

### Named Rules
**The Machine Voice Rule.** Geist Mono is for data the pipeline read or produced: document text, unit ids, scores, file names. Fixed settings such as the clue weights, explanation, labels and headings are always Geist, with tabular figures where numbers line up.

**The Bold Figure Rule.** In running text, emphasis goes on the number or the condition that matters ("28 of 28", "at least two different clues"), set in 600 Ledger Ink against Second Ink prose. No italics, no colour emphasis.

## Layout

Full-width bands, each capped at 1600px and padded by a fluid gutter (16 to 64px). Bands are separated by a single top hairline and fluid vertical padding (56 to 88px).

The first viewport is a two-column grid at roughly 41/59: headline, lede, actions and fact lines on the left, the demo panel on the right spanning both rows. Content bands use a 1.25fr / 1fr two-column grid with a fluid 32 to 80px gap. Inside the demo, the document text and the step track sit side by side at roughly 39/61.

The spacing rhythm is a 4px base used at 4, 8, 12, 16, 20 and 24px, with larger fluid gaps between blocks. Fact lines, ledgers and unit lists are stacked rows divided by hairlines rather than boxed.

Responsive behaviour: at 1100px and below the hero stacks (pitch, demo, facts). At 760px and below the header wraps, the demo's text and steps stack, content bands go to one column, the definition list in the fold collapses, and the demo footer wraps.

## Elevation & Depth

Flat by default. Depth comes from hairlines and the Soft Grey recess, not shadows. Outlines on quiet buttons, the replay button, the meter and pending step rings are drawn as 1px or 1.5px inset rings so they do not shift layout.

### Shadow Vocabulary
- **Panel lift** (`box-shadow: 0 1px 2px rgba(22, 24, 29, 0.04), 0 12px 32px -12px rgba(22, 24, 29, 0.12)`): the demo panel only, so the working object reads as the one thing to touch. Ink-tinted and soft; never coloured.

### Named Rules
**The One Raised Object Rule.** A page has at most one lifted surface, and it is the thing the visitor operates: the demo panel on the front page, the document panel in the review app, the token panel on the reviewer sign-in. Everything else sits flat on hairlines.

## Shapes

Square by default. An 8px radius is reserved for the raised panel of each page (the demo panel, the review document panel, the token panel) and for buttons (primary, quiet, replay). Text inputs, selects and textareas are square. Step markers are full circles because they are counters. The document text block, outcome boxes, tables, the meter and the "not built yet" slot have square corners. Borders are 1px throughout; the selected tab and the meter's threshold line are the only 2px strokes.

## Components

### Buttons
Solid, compact, unadorned.
- **Shape:** 8px radius, 44px minimum height, 18px side padding, 15px at weight 600.
- **Primary:** Ledger Ink fill, white text; one per view, for the main action.
- **Hover / Focus:** fill eases to Ink Hover over 120ms; focus is the shared 2px Focus Blue outline at a 3px offset.
- **Quiet:** Paper fill, Ledger Ink text, a 1px Strong Hairline inset ring; hover fills Soft Grey. Used beside the primary for the secondary route.
- **Replay:** the quiet treatment at small size (6px by 12px padding) inside the demo footer.
- **Small quiet:** 36px high, 12px side padding, 14px text; the review queue's refresh button.
- **Disabled:** Soft Grey fill, Muted Ink text, a 1px Hairline ring, default cursor.

### Navigation
A 64px top bar with a hairline under it: the wordmark "Docket" in Geist 700 at 19px on the left, plain text links in Second Ink at 15px on the right that darken to Ledger Ink on hover, with no underline. On narrow screens the bar wraps to two lines. Body links elsewhere are underlined with a 1px Strong Hairline line that turns to the text colour on hover.

### Tabs
A Soft Grey strip with a hairline under it; tabs are plain text in Second Ink. The selected tab turns Ledger Ink at 600 with a 2px ink underline sitting on the strip's hairline. Arrow keys move between tabs. The strip scrolls sideways without a scrollbar when tabs overflow.

### Demo panel (signature)
The page's one raised object: Paper ground, 1px Strong Hairline border, 8px radius, Panel lift. It holds the tab strip, then the document text beside the step track, then a Soft Grey footer stating the data source.
- **Document text:** a square Soft Grey block with a hairline border, Geist Mono at 13px. Phrases that produced a clue carry the Clue Mark highlight with a 1px edge ring. A document with no text shows a centred Geist note instead.
- **Step track:** four numbered steps on a 1px vertical hairline. Pending steps show a ringed counter and a muted title with the detail hidden. Each step turns done in sequence (520ms apart): counter fills with Ledger Ink, detail rises 4px and fades in over 360ms. When the run settles, every counter takes the outcome colour.
- **Score meter:** an 8px square bar on Soft Grey with a hairline ring, an ink fill that eases to the score over 500ms, and a 2px threshold line labelled with the real filing line.
- **Outcome box:** a square block in filed-bg or held-bg; the heading in the outcome colour at 600, the reason in its darker text tone. Below it, a hairline-ruled ledger states who decided, what the model suggested ("not used to file"), and the test label.
- **Not-built slot:** a small square tag with a 1px dashed Strong Hairline border in the footer, labelling the planned "try your own PDF" mode. Dashed borders mean "planned, not built" and are used nowhere else.

### Tables
Full width, hairline under every row, left-aligned, no fills or zebra stripes. Header cells at 13px in Second Ink; row headers in Second Ink at weight 400. Numeric columns align right in Geist with tabular figures; comparison figures set at 600 with a small Second Ink note under them.

### Fold
A `details` block under a hairline for research terms and method. The summary is a 600 title with a small drawn chevron that rotates over 150ms. Its contents are a two-column definition list that collapses to one column on narrow screens.

### Reviewer page

The Operate surface. The same tokens, fonts and rules as the front page; no new colours or components beyond those named here.
- **Token form (local only; the beta opens without it):** a 25rem raised panel (Strong Hairline border, 8px radius, Panel lift) placed high on a white page: the wordmark at 19px 700, one line of Second Ink, a square 44px input in Geist Mono, a full-width primary button, and a status line in Second Ink below it.
- **Top bar:** the wordmark as the page heading and "Document review" in Second Ink beside it, with a hairline under the bar.
- **Status line:** one line of 15px Ledger Ink under the top bar for saving, results and request errors; it takes no space when empty. Errors are plain ink, never red or amber.
- **Sample panel (beta only):** "Try a sample" sits above the queue in the same 20rem column, stacked above it at 390px. A 15px 600 title, a 13px Second Ink line saying what a sample is, a square select labelled "Kind of document", and a filled "Send a sample document" button. It is the page's one filled button: sending a sample is not a review decision, so it does not break the rule that no review choice reads as recommended. Under the button, a 14px Ledger Ink status line for sending, the outcome and refusals; it takes no space when empty, and refusals stay plain ink. When a send is refused because the sandbox is in its last five minutes, a small quiet "Start fresh" button appears under the status line; it deletes the sandbox and sends the same sample again. Under that, a 13px Second Ink line about the visitor's sandbox: "Private to this browser · starts with your first sample", then, once one exists, "Private to this browser · deleted in N minutes" with a small quiet "Delete now" button. While either the send or Delete now waits for its answer, the other is disabled. A hairline closes the panel and bleeds as far as the queue rows. If the sample list cannot load, only the status line and the sandbox line remain. If the browser blocks cookies, the select, the button and the sandbox line are hidden, and the status line says the sandbox needs a cookie to stay private.
- **Queue:** a 20rem column with no box. A 15px 600 title, the count in 13px Second Ink, and a small quiet refresh button. Each document is a hairline-ruled row: the file name in Geist Mono 13px, the sender in 13px Second Ink. The open document's row sits on Soft Grey with its file name at weight 650. Hover is Soft Grey.
- **Document panel (the raised object):** a header with the file name in Geist Mono 17px, the status tag, and the sender; then the PDF preview beside the evidence column at roughly 51/49, stacked under 1100px; then the trace fold. The preview is a square frame on Soft Grey with an "Open PDF" link under it.
- **Status tag:** a square Geist Mono 13px tag. Held Amber on held-bg for `needs_review`, Filed Green on filed-bg for `filed`, Second Ink on Soft Grey for anything else.
- **Why it waits:** the outcome box from the front page: held-bg, "Waiting for a person" at 600 in Held Amber, the plain-language reason in held-text, and the rules' raw reason in Geist Mono under it, labelled "Hold reason:". When another hold reason won but the shortlist step recorded a conflict, the conflict's plain-language line sits above the raw reason. A filed document gets the filed box; a dismissed one gets the same box on Soft Grey in Second Ink. A document still being read gets the Soft Grey box with "Docket is reading this document." and its raw reason under it. A sample that stopped for the budget or the kill switch gets the Soft Grey box headed "Not finished", the plain reason, and the raw reason under it.
- **Candidates:** a hairline-ruled list. Each row: unit id in Geist Mono 500, then the score and its tier in plain words (strong evidence, some evidence, and for tier none "weak evidence, below the medium line"; a row whose own score is 0 with no conflict shows just "Score 0") in Geist Mono Second Ink; a line naming "Rules' top pick" and "Model suggestion" in Second Ink; then the clues found with their strengths to two decimals. On a conflict, a 13px Second Ink note under the heading says every score is set to 0 because the clues name more than one unit outright, and each row reads "Score 0, set by the conflict" instead of a tier.
- **Picks:** two columns split by a hairline, the rules' top pick first and the model's suggestion second. Each has a 15px 600 heading, the unit id in Geist Mono 15px, a 13px Second Ink note (shown only when the card has a unit; the rules' note says "The rules list this unit first.", plus "Compare the candidates with the document." on a conflict; the suggestion's note says nothing is filed unless the reviewer approves it), and a quiet approve button. They stack under 760px.
- **Actions:** "Pick another candidate" (square select in Geist Mono) and "Dismiss reason" (square textarea), each with its quiet button beside it, stacked under 760px. Every review button is quiet, so no single choice reads as the recommended one.
- **Empty states:** with documents in the queue and none open, the panel says to open the oldest one. With an empty queue the queue shows only its 0 count, and the panel, drawn flat with a Hairline border and no lift, says nothing is waiting and to refresh.

### Fact lines
Short lines of plain text, each with a hairline above it, the real figure bolded in Ledger Ink, the sentence in Second Ink.

## Do's and Don'ts

### Do:
- **Do** take every figure from the recorded data and bold the figure, not the claim around it.
- **Do** use Filed Green and Held Amber only for a decision, and show who decided (rules, model, person) beside it.
- **Do** set pipeline output (document text, unit ids, scores) in Geist Mono at 13px; everything else, fixed settings included, in Geist.
- **Do** separate content with 1px hairlines and the Soft Grey recess before reaching for a box or a shadow.
- **Do** keep the 8px radius for the operated panel and buttons; leave everything else square.
- **Do** give every animated step a static, readable final state, shown at once under reduced motion.
- **Do** use the shared focus ring (2px Focus Blue, 3px offset) on every interactive element.

### Don't:
- **Don't** use gradients, gradient text, glows or coloured shadows.
- **Don't** add a split hero with a glowing product screenshot, a logo row, testimonials or invented customers.
- **Don't** lift more than one surface per page with the panel shadow.
- **Don't** colour a held document red or style it as an error.
- **Don't** reuse the dashed border for anything but a labelled, not-yet-built slot.
- **Don't** make one review action look recommended: the approve buttons stay quiet, and the model's suggestion is always labelled as never filing.
- **Don't** use Held Amber or Filed Green on the reviewer page for anything but the status tag and the outcome box.
