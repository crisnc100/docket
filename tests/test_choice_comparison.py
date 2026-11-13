from dataclasses import replace

import pytest

from docket.core.scoring import load_scoring_config
from docket.evaluation import Outcome, compare_choices, summarize, write_report
from tests.test_evaluation import label


@pytest.mark.parametrize(
    "new_status,old_status,new_record,old_record,new_category,expected",
    [
        ("would_file", "filed", "UNIT-0001", "UNIT-0001", "lease", (0, 0, 0, 0)),
        ("would_review", "would_review", "UNIT-0001", "UNIT-0002", "lease", (0, 0, 1, 0)),
        ("would_review", "would_review", "UNIT-0001", "UNIT-0001", "misc", (0, 0, 0, 1)),
        ("would_file", "would_review", "UNIT-0001", "UNIT-0001", "lease", (1, 0, 0, 0)),
        ("would_review", "would_file", "UNIT-0001", "UNIT-0001", "lease", (1, 0, 0, 0)),
        ("would_file", "would_file", "UNIT-0001", "UNIT-0002", "lease", (0, 1, 0, 0)),
    ],
)
def test_choice_comparison_separates_routing_records_proposals_and_categories(
    new_status, old_status, new_record, old_record, new_category, expected
):
    old = Outcome("doc-1", old_status, "test", old_record, "lease", ".6", "strong")
    new = replace(old, status=new_status, record_id=new_record, category=new_category)
    routing, filed_record, proposal, category = expected
    changed = routing + filed_record
    assert compare_choices([new], [old]) == {
        "documents": 1,
        "filing_decision_changes": changed,
        "routing_changes": routing,
        "filed_record_changes": filed_record,
        "review_proposal_changes": proposal,
        "category_changes": category,
        "changed_document_ids": ["doc-1"] if changed else [],
    }


def test_choice_comparison_rejects_missing_or_misaligned_documents():
    old = Outcome("doc-1", "would_file", "test", "UNIT-0001", "lease", ".6", "strong")
    with pytest.raises(ValueError):
        compare_choices([old], [])
    with pytest.raises(ValueError, match="document order"):
        compare_choices([replace(old, document_id="doc-2")], [old])


def test_report_states_choice_effect_and_keeps_category_difference_separate(tmp_path):
    old = Outcome("doc-1", "would_review", "test", "UNIT-0001", "lease", "0", "none")
    new = replace(old, record_id="UNIT-0002", category="misc")
    baseline = summarize([label()], [old], load_scoring_config())
    report = {
        "date": "test",
        "seed": 7,
        "status": "PASS",
        "docket": baseline,
        "baseline": baseline,
        "floors": {},
        "choice_comparison": compare_choices([new], [old]),
    }
    path = write_report(report, tmp_path)
    text = path.with_suffix(".md").read_text()
    assert "filing decisions differ from the rules-only baseline on 0 of 1 documents." in text
    assert "Review proposals differ on 1 documents" in text
    assert "Category labels differ on 1 documents" in text
    assert "Baseline distinct points: 1. Docket distinct points: 1." in text
