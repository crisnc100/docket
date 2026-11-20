# Advisory brief: guarding the nightly reset against in-flight samples

## THE ASK
Docket's hosted beta PR C adds `docket beta-reset`, which runs nightly. The plan is builds/beta/plan.md, PR C Build 1. In order, the reset:
1. sets a "resetting" flag, so new samples get a 503;
2. terminates open workflows;
3. clears documents, sources, events and filings, and empties the bucket;
4. prunes the counters;
5. reseeds 12 documents;
6. clears the flag.

## WHAT WE KNOW
- `POST /beta/samples` (docket/api.py, docket/beta_samples.py) checks the switch and the ledger, reserves cap slots in a transaction, generates a PDF and submits it through Ingress (docket/ingress.py). Ingress writes the document row, stores the PDF and starts a workflow.
- The window: a sample passes the flag check, the reset then sets the flag, terminates workflows and clears the tables, and then that sample's ingress writes a document whose workflow runs against deleted rows. Or it starts after the terminate step and survives the reset half-cleared.
- The workflows to terminate come from the documents table (non-terminal rows), the way reconcile does it.
- The beta is a low-traffic demo with a few invitees. The reset runs at 08:00 UTC.

## CURRENT DIRECTION
The builder proposes a Postgres advisory lock. `POST /beta/samples` holds it shared from its state check through ingress. The reset sets the flag, then takes the lock exclusive once, which waits for in-flight samples to finish, and then proceeds.
The builder's simpler alternative: no lock, and a second terminate pass after the clear. That leaves a millisecond window.

## DECISIONS IN PLAY
- Is the advisory lock right, or is there a simpler correct guard?
- If a lock: are there deadlock or long-hold risks? For example, the shared lock is held across PDF generation, S3 writes and the workflow start, and a stuck S3 call would block the reset.

## READ FIRST
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/beta_samples.py
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/api.py (the /beta/samples route)
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/ingress.py
- /Users/cortega/Documents/Projects/docket/docket-beta/docket/cli.py (the reconcile command)
- /Users/cortega/Documents/Projects/docket/docket-beta/builds/beta/plan.md (the PR C section)
