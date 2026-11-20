"""The beta load run: samples, review actions and one reset, while docker stats is sampled.

    uv run python scripts/load/beta_load.py <env file> [--minutes 10] [--samples 60]

Start the stack first with the stub provider:

    docker compose -f compose.prod.yaml -f scripts/load/compose.load.yaml \
        --env-file <env file> up -d --build --wait

Samples come from four test addresses, 15 each, so no address reaches its 20 an hour and
the caps stay as the beta runs them. The worker's model calls go to the stub, so the
ledger charges token prices for answers nobody bills. Prints the peak memory and CPU per
container, and never a setting's value.
"""

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import httpx
from pydantic import SecretStr

from docket.beta import SESSION_COOKIE, session_value
from docket.corpus import SAMPLE_CASE_TYPES
from docket.settings import Settings

ADDRESSES = [f"198.51.100.{index}" for index in (11, 12, 13, 14)]
COMPOSE = ["docker", "compose", "-f", "compose.prod.yaml", "-f", "scripts/load/compose.load.yaml"]


def env_values(env_file: Path) -> dict[str, str]:
    values = {}
    for line in env_file.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[name.strip()] = value.strip()
    return values


def megabytes(text: str) -> float:
    number = float(text.rstrip("KMGiB"))
    unit = text[len(text.rstrip("KMGiB")) :]
    return number * {"B": 1 / 2**20, "KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[unit]


async def sample_stats(peaks: dict, stop: asyncio.Event) -> None:
    while not stop.is_set():
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            row = json.loads(line)
            if not row["Name"].startswith("docket-hosted-"):
                continue
            used, limit = (part.strip() for part in row["MemUsage"].split("/"))
            name = row["Name"].removeprefix("docket-hosted-").removesuffix("-1")
            peak = peaks.setdefault(name, {"memory_mb": 0.0, "cpu_percent": 0.0})
            peak["memory_mb"] = max(peak["memory_mb"], megabytes(used))
            peak["limit_mb"] = megabytes(limit)
            peak["cpu_percent"] = max(peak["cpu_percent"], float(row["CPUPerc"].rstrip("%")))
        await asyncio.sleep(1)


async def review_one(http: httpx.AsyncClient, headers: dict, outcomes: dict) -> None:
    items = (await http.get("/review", headers=headers)).json()["items"]
    if not items:
        return
    item = random.choice(items)
    if item["candidates"] and random.random() < 0.6:
        action, body = "approve", {"record_id": item["candidates"][0]["record_id"]}
    else:
        action, body = "dismiss", {"reason": "Load run: not this record."}
    response = await http.post(
        f"/review/{item['document_id']}/{action}", json=body, headers=headers
    )
    key = f"{action} {response.status_code}"
    outcomes[key] = outcomes.get(key, 0) + 1


async def run(env_file: Path, minutes: float, samples: int) -> dict:
    values = env_values(env_file)
    settings = Settings(
        _env_file=None,
        docket_invite_codes=SecretStr(values["DOCKET_INVITE_CODES"]),
        docket_session_key=SecretStr(values["DOCKET_SESSION_KEY"]),
    )
    code = values["DOCKET_INVITE_CODES"].split(",")[0].strip()
    cookie = f"{SESSION_COOKIE}={session_value(settings, code)}"
    origin = values["DOCKET_BASE_URL"]
    base = f"http://127.0.0.1:{values.get('DOCKET_PORT') or '8001'}"
    peaks: dict = {}
    stop = asyncio.Event()
    stats = asyncio.create_task(sample_stats(peaks, stop))
    sent, refused, reviews, settled = [], {}, {}, {}
    reset_output = ""
    interval = minutes * 60 / samples
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=base, timeout=30) as http:
        for index in range(samples):
            headers = {
                "Cookie": cookie,
                "Origin": origin,
                "X-Forwarded-For": ADDRESSES[index % len(ADDRESSES)],
            }
            case_type = SAMPLE_CASE_TYPES[index % len(SAMPLE_CASE_TYPES)]
            response = await http.post(
                "/beta/samples", json={"case_type": case_type}, headers=headers
            )
            if response.status_code == 200:
                sent.append(response.json()["document_id"])
            else:
                key = str(response.status_code)
                refused[key] = refused.get(key, 0) + 1
            if index % 3 == 2:
                await review_one(http, headers, reviews)
            if index == samples // 2:
                reset = await asyncio.to_thread(
                    subprocess.run,
                    [*COMPOSE, "--env-file", str(env_file), "run", "--rm", "--no-deps", "reset"]
                    + ["docket", "beta-reset"],
                    capture_output=True,
                    text=True,
                )
                reset_output = (reset.stdout + reset.stderr).strip()
            wait = started + (index + 1) * interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        await asyncio.sleep(20)
        for document_id in sent:
            trace = await http.get(f"/documents/{document_id}", headers=headers)
            status = (
                trace.json()["status"] if trace.status_code == 200 else f"http {trace.status_code}"
            )
            settled[status] = settled.get(status, 0) + 1
        health = (await http.get("/healthz")).json()
    stop.set()
    await stats
    return {
        "minutes": round((time.monotonic() - started) / 60, 1),
        "samples_accepted": len(sent),
        "samples_refused": refused,
        "sample_outcomes": settled,
        "review_actions": reviews,
        "reset": reset_output,
        "health": health,
        "peaks": peaks,
        # The stub stands in for the provider, so it is not part of the beta's total.
        "peak_total_mb": round(
            sum(peak["memory_mb"] for name, peak in peaks.items() if name != "stub"), 1
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--samples", type=int, default=60)
    args = parser.parse_args()
    result = asyncio.run(run(args.env_file, args.minutes, args.samples))
    print(json.dumps(result, indent=2))
    over = [name for name, peak in result["peaks"].items() if peak["memory_mb"] >= peak["limit_mb"]]
    if over:
        sys.exit("FAIL: at the memory limit: " + ", ".join(over))


if __name__ == "__main__":
    main()
