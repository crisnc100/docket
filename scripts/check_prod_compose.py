"""Check the rendered compose.prod.yaml without printing any of its values.

    uv run python scripts/check_prod_compose.py <env file>

The rendered file holds every password and key, so this prints only what each check
found, by service and setting name.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

LONG_RUNNING = {"temporal", "postgres", "seaweedfs", "api", "worker", "reset"}
DOCKET = {"api", "worker", "reset"}
# The six long-running services' limits together: 3.3 GB.
TOTAL_LIMIT = (768 + 512 + 512 + 512 + 768 + 256) * 1024 * 1024
SECRETS = ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY", "ANTHROPIC_API_KEY")
SECRETS += ("DOCKET_ADDRESS_KEY",)


def render(env_file: Path) -> dict:
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.prod.yaml", "--env-file", str(env_file)]
        + ["config", "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # A missing setting is named without its value, but a line of the env file that
        # compose cannot parse may be quoted in its error, value included.
        sys.exit("compose config failed:\n" + result.stderr.strip())
    return json.loads(result.stdout)


def env_values(env_file: Path) -> dict[str, str]:
    values = {}
    for line in env_file.read_text().splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[name.strip()] = value.strip()
    return values


def problems(config: dict, secrets: dict[str, str]) -> list[str]:
    services = config["services"]
    found = []
    if set(services) != LONG_RUNNING | {"temporal-data-init", "migrate"}:
        found.append("the services are not the expected eight")
    for name, service in services.items():
        for port in service.get("ports", []):
            if name != "api":
                found.append(f"{name} publishes a port")
            elif port.get("host_ip") != "127.0.0.1" or port.get("target") != 8000:
                found.append("the api port is not the API on 127.0.0.1")
        if name == "api" and len(service.get("ports", [])) != 1:
            found.append("the api does not publish exactly one port")
        if not service.get("mem_limit") or not service.get("cpus"):
            found.append(f"{name} has no memory or CPU limit")
        logging = service.get("logging", {})
        options = logging.get("options", {})
        if logging.get("driver") != "json-file" or not {"max-size", "max-file"} <= set(options):
            found.append(f"{name} has no log rotation")
        if name in LONG_RUNNING:
            if service.get("restart") != "unless-stopped":
                found.append(f"{name} does not restart unless stopped")
            if not service.get("healthcheck", {}).get("test"):
                found.append(f"{name} has no healthcheck")
        environment = service.get("environment") or {}
        if "ANTHROPIC_API_KEY" in environment and name != "worker":
            found.append(f"{name} receives the model key")
        for setting in environment:
            if setting in {"DOCKET_REVIEW_TOKEN", "ANTHROPIC_BASE_URL", "OPENROUTER_API_KEY"}:
                found.append(f"{name} sets {setting}")
            if setting.startswith("DOCKET_FAILPOINT"):
                found.append(f"{name} sets {setting}")
        if name in DOCKET and environment.get("DOCKET_ENV") != "beta":
            found.append(f"{name} does not run with DOCKET_ENV=beta")
        # Secrets reach a process through its environment only, never its command line.
        visible = json.dumps(
            [service.get(key) for key in ("command", "entrypoint", "healthcheck", "labels")]
        )
        for secret, value in secrets.items():
            if value and value in visible:
                found.append(f"{name} has {secret} on its command line")
    if not services.get("worker", {}).get("environment", {}).get("ANTHROPIC_API_KEY"):
        found.append("the worker has no model key")
    total = sum(int(services[name].get("mem_limit", 0)) for name in LONG_RUNNING & set(services))
    if total > TOTAL_LIMIT:
        found.append("the long-running services' memory limits add up to more than 3.3 GB")
    return found


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: check_prod_compose.py <env file>")
    env_file = Path(sys.argv[1])
    # Compose takes a variable from the shell over the env file.
    shadowed = sorted(name for name in env_values(env_file) if name in os.environ)
    if shadowed:
        sys.exit(
            "FAIL: set in this shell, so compose would not read them from the env file: "
            + ", ".join(shadowed)
        )
    config = render(env_file)
    secrets = {name: value for name, value in env_values(env_file).items() if name in SECRETS}
    found = problems(config, secrets)
    for problem in found:
        print("FAIL: " + problem)
    if found:
        sys.exit(1)
    print(
        f"OK: {len(config['services'])} services. Only the API is published, on 127.0.0.1. "
        "Every service has memory and CPU limits and log rotation. Only the worker gets "
        "the model key. No secret is on a command line."
    )


if __name__ == "__main__":
    main()
