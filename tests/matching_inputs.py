import random

from docket.config import load_records


def double_records():
    records = load_records()
    return records + [
        record.model_copy(update={"record_id": f"UNIT-{len(records) + index + 1:04d}"})
        for index, record in enumerate(records)
    ]


def distinct_capped_text(kind="realistic"):
    rng = random.Random(7)
    streets = ["Cedar", "Pine", "Maple", "Willow", "Oak", "Birch"]
    words = ["rent", "lease", "repair", "notice", "inspect", "tenant"]
    if kind == "realistic":
        records = load_records()
        lines = [
            (
                f"{1000 + index} {rng.choice(streets)} St Apt {rng.randrange(1, 201)}"
                if index % 2
                else f"{index:04d} {rng.choice(records).current_tenants[0]} {rng.choice(words)}"
            )[:31].ljust(31)
            for index in range(2000)
        ]
        lines[-1] += " "
    elif kind == "unit_lines":
        lines = [
            ("#1 " * 7 + f"{index:04d} {rng.choice(words)}")[:31].ljust(31) for index in range(2000)
        ]
        lines[-1] += " "
    else:
        lines = [f"{index:04d} {rng.choice(streets)}".ljust(15) for index in range(1999)]
        prefix, filler = {
            "whitespace": ("Address:", " "),
            "street_separators": ("St", "._"),
            "street_keywords": ("", "st.st.st."),
        }[kind]
        length = 64000 - sum(len(line) + 1 for line in lines)
        lines.insert(0, prefix + (filler * length)[: length - len(prefix) - 1] + "x")
    text = "\n".join(lines)
    assert len(text) == 64000
    assert len(lines) == len(set(lines)) == 2000
    return text
