import json
import shutil
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from docket.cli import app
from docket.llm.archive import check_archive, load_archive, pack_exchange
from docket.llm.errors import BudgetExceeded, ExportPending
from docket.llm.gateway import Gateway, Mode
from docket.prompts import choose_request
from docket.runtime import Runtime
from docket.settings import Settings
from docket.workflows import DocumentInput
from scripts import reproduce_eval
from tests.test_exchange_archive import exchange_files as exchange_files


def test_import_uses_recordings_when_working_folders_are_absent(exchange_files):
    exchange, store, _ = exchange_files
    before = store.path.read_bytes()
    keys = [row.key for row in store.entries()]
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    assert exchange.import_answers(keys, store) == {"imported": 2, "pending": 0}
    assert store.path.read_bytes() == before
    assert not exchange.pending.exists() and not exchange.answers.exists()


async def test_seed_import_without_working_folders_uses_saved_recordings(tmp_path, monkeypatch):
    from docket.evaluation import evaluate

    root = Path.cwd()
    for directory in ("corpus", "config"):
        (tmp_path / directory).symlink_to(root / directory, target_is_directory=True)
    recordings = tmp_path / "evals/history/simulated"
    recordings.mkdir(parents=True)
    for name in ("calls.jsonl", "manifest.json", "exchange.jsonl.gz"):
        shutil.copyfile(root / "evals/history/simulated" / name, recordings / name)
    monkeypatch.chdir(tmp_path)

    async def outcomes(*args, **kwargs):
        raise RuntimeError("import reached replay without working files")

    monkeypatch.setattr("docket.evaluation.model_outcomes", outcomes)
    with pytest.raises(RuntimeError, match="import reached replay"):
        await evaluate(
            11,
            "import",
            Decimal(0),
            settings=Settings(
                _env_file=None,
                docket_model_provider="openrouter",
                docket_model="openai/gpt-4o-mini",
            ),
        )
    assert not Path("evals/pending").exists() and not Path("evals/answers").exists()


def test_partial_pack_preserves_all_previous_archive_entries(exchange_files):
    exchange, store, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    before = load_archive(archive)
    removed = next(iter(before))
    for directory in (exchange.pending, exchange.answers):
        (directory / f"{removed}.json").unlink()
    assert pack_exchange(exchange.pending, exchange.answers, archive) == 2
    assert load_archive(archive) == before
    assert check_archive(archive, store.path) == 2


def test_packing_cannot_overwrite_evidence_with_a_different_recorded_answer(exchange_files):
    exchange, _, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    before = archive.read_bytes()
    answer_path = sorted(exchange.answers.glob("*.json"))[0]
    answer = json.loads(answer_path.read_text())
    answer["output"]["record_id"] = "none"
    answer_path.write_text(json.dumps(answer))
    with pytest.raises(ValueError, match="differs from recording"):
        pack_exchange(exchange.pending, exchange.answers, archive)
    assert archive.read_bytes() == before


def test_repacking_without_working_folders_keeps_identical_archive(exchange_files):
    exchange, store, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    before = archive.read_bytes()
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    assert pack_exchange(exchange.pending, exchange.answers, archive) == 2
    assert archive.read_bytes() == before
    assert check_archive(archive, store.path) == 2


async def test_new_exchange_extends_history_without_old_working_files(exchange_files):
    exchange, store, archive = exchange_files
    pack_exchange(exchange.pending, exchange.answers, archive)
    before = load_archive(archive)
    shutil.rmtree(exchange.pending)
    shutil.rmtree(exchange.answers)
    gateway = Gateway(mode=Mode.EXPORT, model="test-target", store=store, exchange=exchange)
    with pytest.raises(ExportPending):
        await gateway.call(
            **choose_request("new synthetic request", ["UNIT-0001"]), source="synthetic_corpus"
        )
    path = next(exchange.pending.glob("*.json"))
    exchange.answers.mkdir()
    (exchange.answers / path.name).write_text(
        json.dumps(
            {
                "output": {"record_id": "UNIT-0001"},
                "provenance": {"simulated_by": "test-simulator", "standing_in_for": "test-target"},
            }
        )
    )
    assert exchange.import_answers([*before, path.stem], store) == {"imported": 3, "pending": 0}
    assert pack_exchange(exchange.pending, exchange.answers, archive) == 3
    merged = load_archive(archive)
    assert {key: merged[key] for key in before} == before
    assert check_archive(archive, store.path) == 3


@pytest.mark.parametrize(
    ("status", "measurement"),
    [("PENDING-SIMULATION", "no model calls"), ("PASS", "no model calls")],
)
async def test_replay_gate_rejects_unmeasured_or_pending_reports(
    tmp_path, monkeypatch, status, measurement
):
    monkeypatch.chdir(tmp_path)
    folder = Path("evals/reports")
    folder.mkdir(parents=True)
    report = {"date": "2026-11-10", "status": status, "measurement": measurement}
    (folder / "2026-11-10-seed-11.json").write_text(json.dumps(report))

    async def reproduce(*args, **kwargs):
        return report

    monkeypatch.setattr(reproduce_eval, "evaluate", reproduce)
    with pytest.raises(SystemExit, match="measured PASS"):
        await reproduce_eval.main()


def test_live_documents_have_separate_run_budgets_and_a_shared_daily_cap(
    postgres_engine, monkeypatch
):
    monkeypatch.setattr("docket.runtime.OpenRouterTransport", lambda *args: object())
    runtime = Runtime(
        Settings(_env_file=None, docket_model_provider="openrouter", docket_daily_budget_usd=0.015)
    )
    runtime.engine.dispose()
    runtime.engine = postgres_engine
    try:

        def request(document_id):
            return asdict(
                DocumentInput(document_id, "raw/test", "test.pdf", model_mode="live", budget=".01")
            )

        first = runtime.gateway(request("first"))
        assert first.budget is not None
        first.budget.reserve(Decimal(".006"))
        second = runtime.gateway(request("second"))
        assert second.budget is not None
        second.budget.reserve(Decimal(".006"))
        assert first is not second and runtime.gateway(request("first")) is first
        with pytest.raises(BudgetExceeded, match="Per-run"):
            first.budget.reserve(Decimal(".006"))
        third = runtime.gateway(request("third"))
        assert third.budget is not None
        with pytest.raises(BudgetExceeded, match="daily"):
            third.budget.reserve(Decimal(".006"))
        assert third.budget.run.spent == 0
    finally:
        runtime.close()


def test_observe_documents_keep_the_shared_evaluation_budget(postgres_engine, monkeypatch):
    monkeypatch.setattr("docket.runtime.OpenRouterTransport", lambda *args: object())
    runtime = Runtime(Settings(_env_file=None, docket_model_provider="openrouter"))
    runtime.engine.dispose()
    runtime.engine = postgres_engine
    try:

        def request(document_id):
            return asdict(
                DocumentInput(
                    document_id,
                    "raw/test",
                    "test.pdf",
                    mode="observe",
                    run_id="test-eval",
                    model_mode="live",
                    budget=".01",
                )
            )

        first = runtime.gateway(request("first"))
        assert first.budget is not None
        first.budget.reserve(Decimal(".006"))
        second = runtime.gateway(request("second"))
        assert second is first and second.budget is not None
        with pytest.raises(BudgetExceeded, match="Per-run"):
            second.budget.reserve(Decimal(".006"))
    finally:
        runtime.close()


def test_shadow_diff_defaults_to_newest_held_out_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = Path("evals/reports")
    folder.mkdir(parents=True)
    for name in ("2026-11-09-seed-11", "2026-11-10-seed-11", "2026-11-11-seed-7"):
        (folder / f"{name}.json").write_text(
            json.dumps(
                {"measurement": "simulated", "shadow": {"status": name, "disagreements": []}}
            )
        )
    result = CliRunner().invoke(app, ["shadow-diff"])
    assert result.exit_code == 0, result.output
    assert "2026-11-10-seed-11-shadow.json" in result.output
    assert not (folder / "2026-11-11-seed-7-shadow.json").exists()
    explicit = CliRunner().invoke(
        app, ["shadow-diff", "--report", str(folder / "2026-11-11-seed-7.json")]
    )
    assert explicit.exit_code == 0, explicit.output
    assert (folder / "2026-11-11-seed-7-shadow.json").exists()
