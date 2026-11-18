"""The front page shows recorded pipeline results; these tests keep its copy honest."""

import json
import re
from decimal import Decimal
from html.parser import HTMLParser
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


class _Facts(HTMLParser):
    """Pairs each data-fact attribute, in either quote style, with its element's text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open, self.facts = [], []

    def handle_starttag(self, tag, attrs):
        path = dict(attrs).get("data-fact")
        self.open.append([tag, path, ""])

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        while self.open:
            name, path, text = self.open.pop()
            if path is not None:
                self.facts.append((path, text))
            if self.open:
                self.open[-1][2] += text
            if name == tag:
                break

    def handle_data(self, data):
        if self.open:
            self.open[-1][2] += data


def test_every_number_written_into_the_page_matches_demo_json():
    data = json.loads(DEMO.read_text())
    parser = _Facts()
    parser.feed(PAGE.read_text())
    facts = parser.facts
    assert len(facts) >= 20
    for path, shown in facts:
        value = _lookup(data, path)
        if path.startswith("policy.weights."):
            assert Decimal(shown) == Decimal(value), path
        else:
            assert shown == str(value), path


def test_clue_spans_are_real_text_and_cover_every_clue_the_page_marks():
    for document in front_demo.build()["documents"]:
        for span in document["clue_spans"]:
            assert span["text"] in document["text"], (document["id"], span)
        marked = {(span["record_id"], span["signal"]) for span in document["clue_spans"]}
        for candidate in document["candidates"]:
            for signal in candidate["signals"]:
                assert (candidate["record_id"], signal) in marked, (document["id"], signal)


def test_close_spellings_are_marked_where_the_matcher_found_them():
    documents = {doc["id"]: doc for doc in front_demo.build()["documents"]}
    spans = {span["text"] for span in documents["doc-0075"]["clue_spans"]}
    assert "1110 Maple Streeet Apt 4" in spans
    assert "1110 Maple Street Apt 20" in spans


class _VisibleText(HTMLParser):
    """Collects text a visitor reads, minus data-fact elements (pinned by the test above) and
    the demo's data-field placeholders, which home.js replaces with recorded corpus text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.stack, self.parts = 0, [], []

    def handle_starttag(self, tag, attrs):
        hidden = tag in {"script", "style"} or any(
            name in {"data-fact", "data-field"} for name, _ in attrs
        )
        self.stack.append(hidden)
        self.skip += hidden

    def handle_endtag(self, tag):
        if self.stack:
            self.skip -= self.stack.pop()

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def test_no_figure_on_the_page_escapes_the_data_fact_check():
    parser = _VisibleText()
    parser.feed(PAGE.read_text())
    text = " ".join(parser.parts)
    # A licence name and a model version, not measured figures.
    allowed = {"Apache-2.0.", "5.5"}
    stray = [word for word in re.findall(r"\S*\d\S*", text) if word not in allowed]
    assert stray == [], stray
