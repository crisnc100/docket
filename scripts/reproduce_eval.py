import asyncio
import json
from decimal import Decimal
from pathlib import Path

from docket.evaluation import evaluate
from docket.settings import Settings


def require_measured_pass(report: dict) -> None:
    if (
        report.get("status") != "PASS"
        or report.get("measurement") not in {"simulated", "mixed", "openrouter", "anthropic"}
        or not report.get("docket")
        or not report.get("docket_results")
        or not report.get("floors")
        or not all(value is True for value in report["floors"].values())
    ):
        raise SystemExit("FAIL: held-out report must be a measured PASS")


async def main():
    paths = sorted(Path("evals/reports").glob("*-seed-11.json"))
    if not paths:
        raise SystemExit("FAIL: held-out report missing")
    stored = json.loads(paths[-1].read_text())
    require_measured_pass(stored)
    # Replay under the stored report's own model and archive, whatever the defaults are now.
    settings = Settings(
        docket_model=stored["model"],
        docket_model_provider="anthropic" if stored["measurement"] == "anthropic" else "openrouter",
        docket_replay_path="",
    )
    reproduced = await evaluate(11, "replay", Decimal(0), settings=settings, date=stored["date"])
    require_measured_pass(reproduced)
    for name in [
        "status",
        "model",
        "calibration",
        "threshold",
        "baseline",
        "docket",
        "baseline_results",
        "docket_results",
        "floors",
        "shadow",
        "measurement",
        "provenance",
        "simulation",
        "model_cost_usd",
        "choice_comparison",
        "review_suggestion_comparison",
    ]:
        if reproduced.get(name) != stored.get(name):
            raise SystemExit(f"FAIL: replay report differs in {name}")
    print(f"Replay report reproduced: {reproduced['status']}; seed=11; no model calls")


if __name__ == "__main__":
    asyncio.run(main())
