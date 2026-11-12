import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Docket document intake tools."""


@app.command()
def version() -> None:
    typer.echo("docket 0.1.0")


@app.command()
def spike_models() -> None:
    """Check the configured model's per-call enums: two live calls (Anthropic budget $0.05)."""
    from pathlib import Path

    from docket.llm import spike as spike_module
    from docket.settings import Settings

    settings = Settings()
    anthropic = settings.docket_model_provider == "anthropic"
    name = "ANTHROPIC_API_KEY" if anthropic else "OPENROUTER_API_KEY"
    if not settings.api_key().get_secret_value():
        typer.echo(f"PENDING: {name} is not set; no live calls made.")
        return
    output = "docs/adr/008-spike-result.json" if anthropic else "docs/adr/model-spike-result.json"
    result = spike_module.spike(settings, Path(output))
    typer.echo(result)


corpus_app = typer.Typer(no_args_is_help=True)
app.add_typer(corpus_app, name="corpus")


@corpus_app.command("generate")
def corpus_generate(seed: int = typer.Option(...), output: str | None = None) -> None:
    """Generate deterministic synthetic documents and gold labels."""
    from pathlib import Path

    from docket.corpus import generate

    destination = Path(output) if output else Path(f"corpus/seed-{seed}")
    labels = generate(seed, destination)
    typer.echo(f"Generated {len(labels)} documents: {destination}")


@app.command("score")
def score_pdf(pdf: str = typer.Argument(...)) -> None:
    """Shortlist and score a PDF with deterministic rules only."""
    import json
    from pathlib import Path

    from docket.config import load_records
    from docket.core.candidates import shortlist
    from docket.core.extraction import DocumentRejection, Rejected, extract_document
    from docket.core.scoring import load_scoring_config, score_choice

    path = Path(pdf)
    try:
        extracted = extract_document(path.read_bytes())
    except OSError as exc:
        raise typer.BadParameter(str(exc), param_hint="pdf") from exc
    except DocumentRejection as exc:
        typer.echo(
            json.dumps(
                {
                    "status": exc.status,
                    "reason": exc.reason.value,
                    "candidates": [],
                    "would_auto_file": False,
                }
            )
        )
        raise typer.Exit(1) from exc
    if isinstance(extracted, Rejected):
        typer.echo(
            json.dumps(
                {
                    "status": "rejected",
                    "reason": extracted.reason.value,
                    "candidates": [],
                    "would_auto_file": False,
                }
            )
        )
        raise typer.Exit(1)
    config = load_scoring_config()
    candidates = shortlist(load_records(), text=extracted.text, filename=path.name)
    scored = [
        (candidate, score_choice(candidates, candidate.record_id, config))
        for candidate in candidates.candidates
    ]
    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    selected = scored[0][1] if scored else score_choice(candidates, None, config)
    typer.echo(
        json.dumps(
            {
                "status": "scored",
                "filename": path.name,
                "candidates": [
                    {
                        "record_id": candidate.record_id,
                        "signals": {
                            signal.value: strength for signal, strength in candidate.signals.items()
                        },
                        "score": str(result.score),
                        "tier": result.tier.value,
                        "would_auto_file": result.decision == "file",
                    }
                    for candidate, result in scored
                ],
                "chosen_record_id": selected.record_id,
                "score": str(selected.score),
                "tier": selected.tier.value,
                "conflict": selected.conflict,
                "would_auto_file": selected.decision == "file",
                "reason": selected.reason,
            },
            indent=2,
        )
    )


@app.command()
def worker() -> None:
    """Run the durable document worker."""
    from docket.worker import main

    main()


@app.command()
def reconcile(older_than_minutes: float = 5) -> None:
    """Start interrupted ingress workflows and finalize workflow-less orphans."""
    import asyncio

    from temporalio.client import Client

    from docket.ingress import Ingress
    from docket.runtime import Runtime

    async def run():
        runtime = Runtime()
        try:
            client = await Client.connect(runtime.settings.temporal_address)
            typer.echo(await Ingress(runtime, client).reconcile(older_than_minutes))
        finally:
            runtime.close()

    asyncio.run(run())


@app.command("eval")
def eval_command(seed: int = 11, mode: str = "replay", budget: str = "2.00") -> None:
    """Evaluate a seed in observe mode against the rules-only baseline; --budget caps spend."""
    import asyncio
    from decimal import Decimal

    from docket.evaluation import evaluate, write_report

    report = asyncio.run(evaluate(seed, mode, Decimal(budget)))
    path = write_report(report)
    typer.echo(f"{report['status']}: {path}")
    if report["status"] == "FAIL":
        raise typer.Exit(1)


@app.command("shadow-diff")
def shadow_diff_command(report: str | None = None) -> None:
    """Save a report's shadow comparison to <report>-shadow.json; default: newest seed 11."""
    import json
    from pathlib import Path

    reports = sorted(Path("evals/reports").glob("*-seed-11.json"))
    if report is None and not reports:
        raise typer.BadParameter("No evaluation report exists; run docket eval first")
    path = Path(report) if report else reports[-1]
    data = json.loads(path.read_text())
    output = path.with_name(path.stem + "-shadow.json")
    shadow = dict(data["shadow"])
    if "measurement" in data:
        shadow["measurement"] = data["measurement"]
    if data.get("banner"):
        shadow.update(
            banner=data["banner"],
            provenance=data["provenance"],
            measurement=data.get("measurement", "simulated"),
        )
        if "simulation" in data:
            shadow["simulation"] = data["simulation"]
    output.write_text(json.dumps(shadow, indent=2, sort_keys=True) + "\n")
    typer.echo(f"{data['shadow']['status']}: {output}")


def reviewer_headers() -> dict[str, str]:
    import os

    token = os.environ.get("DOCKET_REVIEW_TOKEN", "")
    if not token or token.isspace():
        typer.echo("Set DOCKET_REVIEW_TOKEN to use the reviewer API.", err=True)
        raise typer.Exit(1)
    return {"Authorization": "Bearer " + token}


@app.command()
def submit(pdf: str = typer.Argument(...), api_url: str = "http://127.0.0.1:8010") -> None:
    """Upload a PDF and print its document id."""
    import json
    from pathlib import Path

    import httpx

    headers = reviewer_headers()
    path = Path(pdf)
    try:
        with path.open("rb") as stream:
            response = httpx.post(
                api_url.rstrip("/") + "/documents",
                files={"file": (path.name, stream, "application/pdf")},
                headers=headers,
                timeout=60,
            )
        response.raise_for_status()
    except (OSError, httpx.HTTPError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(response.json()))


@app.command()
def status(document_id: str = typer.Argument(...), api_url: str = "http://127.0.0.1:8010") -> None:
    """Print a document's status and trace."""
    import json
    import uuid

    import httpx

    headers = reviewer_headers()
    try:
        document_id = str(uuid.UUID(document_id))
        response = httpx.get(
            api_url.rstrip("/") + "/documents/" + document_id, headers=headers, timeout=30
        )
        response.raise_for_status()
    except (ValueError, httpx.HTTPError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(response.json(), indent=2))
