import json
from pathlib import Path

from docket.cost_summary import MODEL, build_summary


def main() -> None:
    summary = build_summary()
    path = Path("evals/cost") / f"{summary['recorded'][-1]}-{MODEL}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    total = summary["total"]
    print(f"Wrote {path}: ${total['spend_usd']} over {total['answered']} answers")


if __name__ == "__main__":
    main()
