"""Deterministic model fixture for process-fault tests, not an eval recording."""

import asyncio
import logging
import os
from pathlib import Path

from docket.runtime import Runtime
from docket.worker import run_worker


class ModelFixture:
    async def call(self, *, activity, schema, **kwargs):
        if activity == "choose" and os.environ.get("DOCKET_TEST_CHOOSE_GATE"):
            print("TEST choose waiting for release", flush=True)
            while not Path("/tmp/docket-release-choose").exists():
                await asyncio.sleep(0.05)
        return schema.model_validate(
            {"category": "lease"} if activity == "classify" else {"record_id": "UNIT-0001"}
        )


class FaultRuntime(Runtime):
    def gateway(self, request):
        return ModelFixture()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(FaultRuntime()))
