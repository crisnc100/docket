import asyncio
import json
from pathlib import Path

import yaml
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from docket.workflows import DocumentInput, FileDocumentWorkflow
from tests.workflow.scenarios import ScenarioActivities


def write_history(history_json: str, path: str = "tests/workflow/history.yaml") -> None:
    # Temporal encodes synthetic activity payloads as base64; retain their bytes
    # and annotate only these non-secret fields rather than excluding the fixture.
    lines = yaml.safe_dump(json.loads(history_json), sort_keys=False, width=10000).splitlines()
    output = []
    for line in lines:
        if line.lstrip().startswith(("data:", "binaryChecksum:")):
            line += " # pragma: allowlist secret"
        output.append(line)
    Path(path).write_text("\n".join(output) + "\n")


async def main():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="history",
            workflows=[FileDocumentWorkflow],
            activities=ScenarioActivities().registered(),
        ):
            for case, path in [
                ("happy", "tests/workflow/history.yaml"),
                ("review", "tests/workflow/review-history.yaml"),
            ]:
                handle = await env.client.start_workflow(
                    FileDocumentWorkflow.run,
                    DocumentInput(
                        "00000000-0000-0000-0000-000000000001", "raw/synthetic-history", case
                    ),
                    id="recorded-" + case,
                    task_queue="history",
                )
                if case == "review":
                    while not (await handle.query(FileDocumentWorkflow.progress))["review_open"]:
                        await asyncio.sleep(0.01)
                    await handle.execute_update(
                        "review_action", {"action": "approve", "value": "UNIT-0001"}
                    )
                assert (await handle.result())["status"] == "filed"
                history = await handle.fetch_history()
                write_history(history.to_json(), path)


if __name__ == "__main__":
    asyncio.run(main())
