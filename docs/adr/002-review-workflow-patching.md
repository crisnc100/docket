# ADR-002: version commands for in-flight reviews

Status: accepted. No workflow-changing deployment has been made yet.

A review may wait across worker deployments. The activity names, order, payloads,
update/signal names, and review timer are part of its recorded command contract.
The current workflow records `workflow.patched("review-contract-v1")` before its
first activity. Its input persists the review timeout, model and mode so a deploy
cannot silently change an existing timer or switch a waiting document's mode.

For a command-changing deployment, introduce a new, uniquely named patch guard
and keep the old branch for histories without that marker. Keep the v1 marker
until old executions are closed and their histories are outside retention; use
Temporal's deprecate_patch procedure before removing a marker. Do not rename an
activity or move a timer across the guard. Changes to review validation that
alter decisions must also be versioned. Terminating a waiting workflow is not a
migration strategy; recorded approvals and dismissals remain authoritative.

Prediction for the next such deployment: both archived happy-path and review
histories replay without nondeterminism, and an already-waiting review keeps its
original timeout and accepts exactly one decision. This future-deploy prediction
has not been measured.

Current-version evidence: scripts/record_workflow_history.py captures actual
Temporal test-server histories, including a review opened and approved through an
update. tests/workflow/test_workflow.py replays both with Temporal Replayer. Its
separate tests exercise signals, timeout, duplicate and invalid updates. These
measure the current contract, not an unbuilt deployment migration.
