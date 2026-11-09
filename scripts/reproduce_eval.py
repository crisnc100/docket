import asyncio
import json
from decimal import Decimal
from pathlib import Path

from docket.evaluation import evaluate


async def main():
    paths = sorted(Path("evals/reports").glob("*-seed-11.json"))
    if not paths:
        raise SystemExit("FAIL: held-out report missing")
    stored = json.loads(paths[-1].read_text())
    reproduced = await evaluate(11, "replay", Decimal(0), date=stored["date"])
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
    ]:
        if reproduced.get(name) != stored.get(name):
            raise SystemExit(f"FAIL: replay report differs in {name}")
    if reproduced["status"] == "FAIL":
        raise SystemExit("FAIL: held-out floors")
    print(f"Replay report reproduced: {reproduced['status']}; seed=11; no model calls")


if __name__ == "__main__":
    asyncio.run(main())
