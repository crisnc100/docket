import json
import subprocess


def test_compose_publishes_every_port_on_loopback_only():
    config = json.loads(
        subprocess.check_output(["docker", "compose", "config", "--format", "json"])
    )
    ports = [
        (service, port)
        for service, spec in config["services"].items()
        for port in spec.get("ports", [])
    ]
    assert {service for service, _ in ports} == {"postgres", "temporal", "seaweedfs"}
    assert len(ports) == 4
    assert all(port.get("host_ip") == "127.0.0.1" for _, port in ports)
