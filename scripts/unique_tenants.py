import csv
from pathlib import Path

from faker import Faker

from docket.core.candidates import normalize_text


def unique_tenants(path: Path) -> None:
    fields = ("current_tenants", "former_tenants")
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    reserved = {
        normalize_text(name)
        for row in rows
        for field in fields
        for name in row[field].split("|")
        if name
    }
    owners: dict[str, str] = {}
    fake = Faker("en_US")
    fake.seed_instance(0)
    for row in rows:
        for field in fields:
            names = row[field].split("|") if row[field] else []
            for index, name in enumerate(names):
                key = normalize_text(name)
                if key in owners and owners[key] != row["record_id"]:
                    while True:
                        name = fake.name()
                        key = normalize_text(name)
                        if key not in reserved:
                            break
                    names[index] = name
                    reserved.add(key)
                owners[key] = row["record_id"]
            row[field] = "|".join(names)
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unique_tenants(Path("config/records.csv"))
