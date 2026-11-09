from datetime import timedelta

from temporalio.testing import WorkflowEnvironment


async def test_time_skipping_environment() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        before = await environment.get_current_time()
        await environment.sleep(timedelta(hours=1))
        after = await environment.get_current_time()
        assert after - before >= timedelta(hours=1)
