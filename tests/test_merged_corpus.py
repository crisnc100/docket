import csv

from docket.config import load_records
from docket.corpus import generate


def test_graded_former_tenant_case_uses_an_eligible_record(tmp_path):
    records = load_records()[:2]
    records[0] = records[0].model_copy(update={"former_tenants": []})
    source = tmp_path / "records.csv"
    with source.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=records[0].model_dump().keys())
        writer.writeheader()
        for record in records:
            row = record.model_dump()
            for field in ("current_tenants", "former_tenants", "aliases"):
                row[field] = "|".join(row[field])
            writer.writerow(row)
    labels = generate(7, tmp_path / "corpus", source)
    assert len(labels) == 180
    former = next(label for label in labels if label.document_id == "doc-0178")
    assert former.case_type == "graded_evidence"
    assert former.record_id == records[1].record_id
