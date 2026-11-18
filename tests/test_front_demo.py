"""The front page shows recorded pipeline results; these tests keep its copy honest."""

import json
import re
from decimal import Decimal
from pathlib import Path

from docket import front_demo

DEMO = Path("web/public/home/demo.json")
PAGE = Path("web/public/home/index.html")


def test_demo_json_matches_the_committed_evidence():
    assert DEMO.read_text() == front_demo.render(), (
        "web/public/home/demo.json is stale: run uv run python -m docket.front_demo"
    )


def test_rules_recomputed_from_each_pdf_match_the_recorded_pipeline_result():
    for document in front_demo.build()["documents"]:
        rules, recorded = document["rules"], document["recorded"]
        assert rules["record_id"] == recorded["record_id"], document["id"]
        assert Decimal(rules["score"]) == Decimal(recorded["score"]), document["id"]
        assert rules["tier"] == recorded["tier"], document["id"]
        assert rules["reason"] == recorded["reason"], document["id"]
        assert (rules["decision"] == "file") == (recorded["status"] == "would_file"), document["id"]


def test_demo_documents_cover_one_filing_and_three_kinds_of_hold():
    documents = {doc["id"]: doc for doc in front_demo.build()["documents"]}
    assert documents["doc-0001"]["recorded"]["status"] == "would_file"
    assert (
        documents["doc-0001"]["recorded"]["record_id"]
        == documents["doc-0001"]["expected"]["record_id"]
    )
    near_miss = documents["doc-0075"]
    assert near_miss["conflict"] is True
    assert near_miss["recorded"]["status"] == "would_review"
    assert near_miss["review_comparison"]["rules_correct"] is False
    assert near_miss["review_comparison"]["suggestion_correct"] is False
    no_text = documents["doc-0012"]
    assert no_text["text"] == "" and no_text["candidates"] == []
    assert no_text["recorded"]["model_called"] is False
    below = documents["doc-0176"]
    assert Decimal(below["rules"]["score"]) < Decimal(
        front_demo.build()["policy"]["auto_file_threshold"]
    )
    assert below["expected"]["decision"] == "filed"


def test_headline_figures_are_the_held_out_report_values():
    report = json.loads(front_demo.HELD_OUT.read_text())
    results = front_demo.build()["results"]
    comparison = report["review_suggestion_comparison"]
    assert results["auto_filed_correct"] == report["docket"]["correct_file_count"]
    assert results["auto_filed_wrong"] == 0
    assert results["model_suggestions_correct"] == comparison["suggestion_correct"]
    assert results["rules_top_pick_correct"] == comparison["rules_correct"]
    gates = front_demo.build()["gates"]
    assert (gates["passed"], gates["total"]) == (2, 4)
    assert gates["results"]["unreadable_at_most_2_percent"] is False
    assert gates["results"]["held_out_floors"] is False


def _lookup(data: dict, dotted: str):
    for key in dotted.split("."):
        data = data[key]
    return data


def test_every_number_written_into_the_page_matches_demo_json():
    data = json.loads(DEMO.read_text())
    facts = re.findall(r'data-fact="([^"]+)"[^>]*>([^<]*)<', PAGE.read_text())
    assert len(facts) >= 20
    for path, shown in facts:
        value = _lookup(data, path)
        if path.startswith("policy.weights."):
            assert Decimal(shown) == Decimal(value), path
        else:
            assert shown == str(value), path
