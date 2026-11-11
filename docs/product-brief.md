# Product brief

## Problem and users

Property staff receive leases, inspections, invoices and notices as PDFs.
Someone must find the right unit, save the file and explain where it went.
Similar addresses, old tenant names and contradictory text make that work risky.

Docket is for the staff who file those documents and the reviewers who resolve uncertain matches.
A record is one known property unit.
An operator maintains the records and runs the service.
The shipped example has 200 synthetic units; it contains no real tenant data.

## What it does

A PDF arrives through an upload or a signed email webhook.
Docket saves the original bytes and starts a workflow: a sequence whose progress survives a worker crash.
It reads the PDF's text and finds up to five candidate records.
A signal is matching evidence, such as an address or tenant name.

Rules score the candidates.
Automatic filing needs two independent signals, a score at or above the configured threshold and no conflict.
A conflict means evidence points to more than one record.
Uncertain documents wait for review, with the PDF, evidence and reason visible together.
A reviewer can approve a known record or dismiss the document with a reason.
Unanswered reviews expire.

A language model labels the document category and suggests a shortlisted record or none.
The suggestion is shown beside the rules' top candidate.
A failed suggestion leaves no suggestion and saves its failure reason in the trace.
The rules still score the document.

Files go to S3-compatible object storage, which stores bytes under a named key.
Postgres keeps document state and an event trace.
Exact-byte resubmissions return the existing document.
Repeated steps use the same keys and database constraints to prevent duplicate filings.

## Deliberate limits

The model never decides whether or where to file automatically.
Only the rules or an explicit human approval decide the record.
The model still supplies the category used in the filing path; a category-call failure ends failed.

Docket does not read page images with optical character recognition (OCR).
It does not merge near-duplicates whose bytes differ.
It does not create records from sender text or follow filing instructions embedded in a PDF.
It does not replace a property-management system or provide individual reviewer accounts.
The local deployment is a development example, not a measured production service.

## How it is measured

A corpus is a labelled set of test documents.
Seeds 7 and 11 each generate 180 documents, including conflicts, injected instructions and broken files.
Seed 7 selected the filing threshold, 0.5857142.
Seed 11 was held out and selected no setting.

Observe mode records what would happen without filing or waiting for a reviewer.
The rules-only baseline takes the highest-scored candidate without the model.
Both use the same filing policy.

The [held-out report](../evals/reports/2026-11-11-seed-11.md) measures:

- Precision: correct filings divided by all automatic filings. Both systems reach 38/38.
- Recall: correct filings divided by the 39 documents labelled for filing. Both reach 38/39.
- Automatic filing and review rates: 38/180 and 102/180. The remaining 40 are rejected.
- Pipeline category accuracy: Docket 124/140 versus the baseline's 132/140 among documents not rejected.
- Correct record suggestions on review items: model 44/102 versus rules 62/102.

All 16 pipeline category misses have empty shortlists and receive misc without a classify call.
Eight are no-record documents; eight have no text layer.
The simulated classify answers are correct on all 120 documents with nonempty shortlists.
These document counts include duplicates, not just unique model calls.
Failed suggestions are excluded from the suggestion comparison and counted separately.
The saved seed-11 run has zero suggestion failures.

The report includes 95% Wilson intervals, uncertainty ranges for each proportion,
a threshold curve and results by case type.
It passes four floors: zero wrong filings, at least 90% clean filing,
zero filings to injected record ids and recall at least equal to the baseline.
The model adds no measured filing gain on this sample.

Model answers are simulated, with each answer's source saved.
They do not measure the target provider's quality, output compliance or cost.
Five [fault tests](adr/007-temporal-recovery.md) measure recovery after process exits and a storage outage.
They check one document, one filing and a final filed status.
[Replay instructions](rerecord.md) explain how to reproduce the evidence.
