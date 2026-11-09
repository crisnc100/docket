import os


def fire(point: str) -> None:
    if os.getenv("DOCKET_FAILPOINT") == point:
        print(f"DOCKET_FAILPOINT fired {point}", flush=True)
        os._exit(137)
