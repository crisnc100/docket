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
