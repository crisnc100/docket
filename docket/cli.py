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
    """Check the configured OpenRouter model's per-call enums (budget: $0.50)."""
    from pathlib import Path

    from docket.llm.spike import spike
    from docket.settings import Settings

    settings = Settings()
    if not settings.openrouter_api_key.get_secret_value():
        typer.echo("PENDING: OPENROUTER_API_KEY is not set; no live calls made.")
        return
    result = spike(settings, Path("docs/adr/model-spike-result.json"))
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
    """Check each Docket/baseline disagreement against the labels; default: newest seed 11."""
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
