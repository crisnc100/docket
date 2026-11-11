from dataclasses import replace

import pytest

from docket.evaluation import compare_review_suggestions
from tests.test_evaluation import label, outcome


def test_review_comparison_counts_abstention_excludes_filings_and_poison():
    labels = [label(), label("no_record", "review", None), label(), label("corrupt", "rejected")]
    for i, item in enumerate(labels):
        item.document_id = str(i)
    model = [replace(outcome("would_review"), document_id=str(i)) for i in range(4)]
    model[1] = replace(model[1], record_id=None)
    model[2] = replace(model[2], status="would_file")
    model[3] = replace(model[3], status="rejected")
    rules = [replace(item, record_id="UNIT-0002") for item in model]
    result = compare_review_suggestions(labels, model, rules)
    assert result["review_documents"] == 2
    assert result["suggestion_correct"] == 2 and result["rules_correct"] == 0
    assert result["suggestion_accuracy"] == 1 and result["rules_accuracy"] == 0
    assert result["suggestion_wilson_95"][0] < 1
    assert result["rules_wilson_95"][1] > 0
    assert [row["document_id"] for row in result["documents"]] == ["0", "1"]
    assert compare_review_suggestions([], [], [])["suggestion_accuracy"] is None
    with pytest.raises(ValueError):
        compare_review_suggestions(labels, model[:-1], rules)
    with pytest.raises(ValueError, match="document order"):
        compare_review_suggestions(labels, list(reversed(model)), rules)


def test_suggestion_is_separate_from_record_used_by_threshold_curve():
    from decimal import Decimal

    from docket.evaluation import compare_choices, metrics

    rule = outcome("would_review", score=".58", tier="strong")
    model = replace(rule, suggestion={"record_id": None})
    assert metrics([label()], [model], Decimal(".55")) == metrics([label()], [rule], Decimal(".55"))
    assert compare_choices([model], [rule])["review_proposal_changes"] == 1
    assert compare_choices([model], [rule])["filing_decision_changes"] == 0
    compared = compare_review_suggestions([label()], [model], [rule])
    assert compared["suggestion_correct"] == 0
    assert compared["rules_correct"] == 1
