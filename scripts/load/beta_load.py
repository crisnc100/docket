"""The beta load run: samples, review actions and one sweep by hand, while docker stats is
sampled.

    uv run python scripts/load/beta_load.py <env file> [--minutes 10] [--samples 60]

It starts its own Compose project, docket-load, from compose.prod.yaml plus
scripts/load/compose.load.yaml, on fresh volumes, and removes it at the end. So it never
shares containers or volumes with a beta on the same machine. Before any sample it checks
that the env file holds no real Anthropic key, that the worker points at the stub, and
that the stub answers from inside the worker. It exits non-zero unless every sample was
accepted and settled, the sweep by hand completed, the stub answered the worker's calls,
and every service was measured under its memory limit.

Samples come from four test addresses, 15 each, so no address reaches its 20 an hour and
the caps stay as the beta runs them. Prints the peak memory and CPU per container, and
never a setting's value.
"""

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import httpx

from docket.corpus import SAMPLE_CASE_TYPES
from docket.sandbox import SANDBOX_COOKIE

ADDRESSES = [f"198.51.100.{index}" for index in (11, 12, 13, 14)]
PROJECT = "docket-load"
SERVICES = {"temporal", "postgres", "seaweedfs", "api", "worker", "sweep", "stub"}
STUB_URL = "http://stub:8080"
HELD = {"needs_review", "filed", "dismissed"}


class LoadRefused(SystemExit):
    pass


def compose(env_file: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", "compose.prod.yaml"]
        + ["-f", "scripts/load/compose.load.yaml", "--env-file", str(env_file), *args],
        capture_output=True,
        text=True,
    )


def in_worker(env_file: Path, code: str) -> str:
    result = compose(env_file, "exec", "-T", "worker", "python", "-c", code)
    if result.returncode != 0:
        raise LoadRefused("FAIL: the worker check could not run")
    return result.stdout.strip()


def confirm_stub(env_file: Path, values: dict[str, str]) -> None:
    """Refuse to send anything unless the worker's model calls can only reach the stub."""
    # Compose takes a variable from the shell over the env file, so a key exported in the
    # shell would reach the worker unseen.
    shadowed = sorted(name for name in values if name in os.environ)
    if shadowed:
        raise LoadRefused(
            "FAIL: set in this shell, so compose would use it: " + ", ".join(shadowed)
        )
    if values.get("ANTHROPIC_API_KEY", "").startswith("sk-ant"):
        raise LoadRefused("FAIL: the env file holds what looks like a real Anthropic key")
    base_url = in_worker(env_file, "import os; print(os.environ.get('ANTHROPIC_BASE_URL', ''))")
    if base_url != STUB_URL:
        raise LoadRefused("FAIL: the worker does not point at the stub")
    answered = in_worker(
        env_file,
        "import json, urllib.request; body = json.dumps({'model': 'stub', 'messages': "
        "[{'role': 'user', 'content': 'UNTRUSTED DOCUMENT:\\nprobe'}]}).encode(); "
        f"print(json.load(urllib.request.urlopen('{STUB_URL}/v1/messages', body))['id'])",
    )
    if answered != "msg_stub":
        raise LoadRefused("FAIL: the stub did not answer the worker")


def stub_requests(env_file: Path) -> int:
    return int(
        in_worker(
            env_file,
            "import json, urllib.request; "
            f"print(json.load(urllib.request.urlopen('{STUB_URL}/count'))['requests'])",
        )
    )


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


async def sample_stats(peaks: dict, failures: list, stop: asyncio.Event) -> None:
    while not stop.is_set():
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            failures.append(result.returncode)
        for line in result.stdout.splitlines():
            row = json.loads(line)
            if not row["Name"].startswith(PROJECT + "-"):
                continue
            used, limit = (part.strip() for part in row["MemUsage"].split("/"))
            name = row["Name"].removeprefix(PROJECT + "-").removesuffix("-1")
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
    # Each address is one browser: its first sample creates its sandbox, and the rest go there.
    cookies: dict[str, str] = {}
    sender: dict[str, str] = {}
    origin = values["DOCKET_BASE_URL"]
    published = compose(env_file, "port", "api", "8000")
    if published.returncode != 0 or not published.stdout.strip().startswith("127.0.0.1:"):
        raise LoadRefused("FAIL: the load project's API is not published on 127.0.0.1")
    base = "http://" + published.stdout.strip()
    confirm_stub(env_file, values)
    peaks: dict = {}
    stat_failures: list = []
    stop = asyncio.Event()
    stats = asyncio.create_task(sample_stats(peaks, stat_failures, stop))
    sent, refused, reviews, settled = [], {}, {}, {}
    sweep = None
    interval = minutes * 60 / samples
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=base, timeout=30) as http:
        for index in range(samples):
            address = ADDRESSES[index % len(ADDRESSES)]
            headers = {"Origin": origin, "X-Forwarded-For": address}
            if address in cookies:
                headers["Cookie"] = cookies[address]
            case_type = SAMPLE_CASE_TYPES[index % len(SAMPLE_CASE_TYPES)]
            response = await http.post(
                "/beta/samples", json={"case_type": case_type}, headers=headers
            )
            # The cookie is Secure, so the client would not send it back over plain http.
            created = response.headers.get("set-cookie", "")
            if created.startswith(SANDBOX_COOKIE + "="):
                cookies[address] = created.split(";", 1)[0]
                headers["Cookie"] = cookies[address]
            if response.status_code == 200:
                document_id = response.json()["document_id"]
                sender[document_id] = address
                sent.append(document_id)
            else:
                key = str(response.status_code)
                refused[key] = refused.get(key, 0) + 1
            if index % 3 == 2:
                await review_one(http, headers, reviews)
            if index == samples // 2:
                sweep = await asyncio.to_thread(
                    compose, env_file, "run", "--rm", "--no-deps", "sweep", "docket", "beta-sweep"
                )
            wait = started + (index + 1) * interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        await asyncio.sleep(20)
        for document_id in sent:
            trace = await http.get(
                f"/documents/{document_id}", headers={"Cookie": cookies[sender[document_id]]}
            )
            status = (
                trace.json()["status"] if trace.status_code == 200 else f"http {trace.status_code}"
            )
            settled[status] = settled.get(status, 0) + 1
        health = (await http.get("/healthz")).json()
    stop.set()
    await stats
    return {
        "minutes": round((time.monotonic() - started) / 60, 1),
        "samples_planned": samples,
        "samples_accepted": len(sent),
        "samples_refused": refused,
        "sample_outcomes": settled,
        "review_actions": reviews,
        "sweep_exit": None if sweep is None else sweep.returncode,
        "sweep": "" if sweep is None else sweep.stdout.strip(),
        "stub_requests": stub_requests(env_file),
        "stats_failures": len(stat_failures),
        "health": health,
        "peaks": peaks,
        # The stub stands in for the provider, so it is not part of the beta's total.
        "peak_total_mb": round(
            sum(peak["memory_mb"] for name, peak in peaks.items() if name != "stub"), 1
        ),
    }


def problems(result: dict) -> list[str]:
    """Every way the run fell short of the workload it was asked to do."""
    found = []
    if result["samples_accepted"] != result["samples_planned"] or result["samples_refused"]:
        found.append(f"samples refused: {result['samples_refused']}")
    # Every sandbox outlives the run, so the sweep deletes none of its samples.
    for outcome, count in result["sample_outcomes"].items():
        if outcome not in HELD:
            found.append(f"{count} samples ended {outcome}")
    if result["sweep_exit"] != 0 or "'complete': True" not in result["sweep"]:
        found.append("the sweep by hand did not complete")
    if result["stub_requests"] < 1:
        found.append("the stub answered no model call")
    if result["stats_failures"]:
        found.append(f"docker stats failed {result['stats_failures']} times")
    missing = SERVICES - set(result["peaks"])
    if missing:
        found.append("no stats for " + ", ".join(sorted(missing)))
    for name, peak in result["peaks"].items():
        if peak["memory_mb"] >= peak["limit_mb"]:
            found.append(f"{name} reached its memory limit")
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--samples", type=int, default=60)
    args = parser.parse_args()
    compose(args.env_file, "down", "-v")
    up = compose(args.env_file, "up", "-d", "--build", "--wait")
    try:
        if up.returncode != 0:
            # A missing setting is named without its value, but a line of the env file that
            # compose cannot parse may be quoted in its error, value included.
            raise LoadRefused("FAIL: the load project did not start:\n" + up.stderr[-2000:])
        result = asyncio.run(run(args.env_file, args.minutes, args.samples))
    finally:
        compose(args.env_file, "down", "-v")
    print(json.dumps(result, indent=2))
    found = problems(result)
    if found:
        sys.exit("FAIL: " + "; ".join(found))


if __name__ == "__main__":
    main()
