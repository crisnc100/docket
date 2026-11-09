import asyncio
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from docket.activities import Activities
from docket.runtime import Runtime
from docket.workflows import FileDocumentWorkflow


async def run_worker(runtime: Runtime | None = None) -> None:
    runtime = runtime or Runtime()
    runtime.ensure_bucket()
    client = await Client.connect(runtime.settings.temporal_address)
    activities = Activities(runtime)
    try:
        async with Worker(
            client,
            task_queue=runtime.settings.docket_task_queue,
            workflows=[FileDocumentWorkflow],
            activities=activities.registered(),
        ):
            Path("/tmp/docket-worker-ready").touch()
            print("Docket worker ready", flush=True)
            await asyncio.Event().wait()
    finally:
        Path("/tmp/docket-worker-ready").unlink(missing_ok=True)
        runtime.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
