"""Pricing (fetched + overrides), token metering, history-based estimates, and usage reports."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from assetforge.config import Settings
from assetforge.core import metering, pricing, reporting, settings_store
from assetforge.core.report_html import render
from assetforge.errors import InvalidInputError, StorageError
from assetforge.image import engine, providers
from assetforge.image.generate import ImageRequest, generate_image
from assetforge.image.providers.base import ProviderResponse
from assetforge.image.providers.fake import FakeProvider

from conftest import run_cli

CATALOG = {
    "gpt-image-2.5-flare": {"litellm_provider": "openai", "mode": "image_generation", "input_cost_per_token": 5e-06,
                            "input_cost_per_image_token": 8e-06, "output_cost_per_image_token": 3.5e-05},
    "gemini/gemini-3.1-flash-image": {"litellm_provider": "gemini", "mode": "image_generation",
                                      "input_cost_per_token": 5e-07, "output_cost_per_image_token": 6e-05,
                                      "output_cost_per_token": 3e-06, "output_cost_per_image": 0.045},
    "azure/gpt-image-2": {"litellm_provider": "azure", "mode": "image_generation", "output_cost_per_image_token": 1},
    "gpt-5": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 1e-06},
}  # fmt: skip


@pytest.fixture
def online(monkeypatch):
    """Serve CATALOG instead of the internet."""
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(CATALOG).encode())

    monkeypatch.setattr(pricing.urllib.request, "urlopen", fake_urlopen)
    return calls


# ---------------------------------------------------------------------- pricing


def test_parse_catalog_keeps_only_openai_and_gemini_image_models():
    models = pricing.parse_catalog(CATALOG)
    assert set(models) == {"openai/gpt-image-2.5-flare", "gemini/gemini-3.1-flash-image"}
    assert models["openai/gpt-image-2.5-flare"] == {"input_text": 5.0, "input_image": 8.0, "output_image": 35.0}
    assert models["gemini/gemini-3.1-flash-image"]["per_image"] == 0.045


def test_update_snapshot_precedence_and_overrides(lib, online):
    rates, source = pricing.rates_for(lib, "openai", "gpt-image-2.5-flare")
    assert rates["output_image"] == 30.0 and source == "builtin"
    first = pricing.update(lib)
    assert first["first"] and first["models"] == 2 and online
    rates, source = pricing.rates_for(lib, "openai", "gpt-image-2.5-flare-2026-09-08")  # dated snapshot → family
    assert rates["output_image"] == 35.0 and source.startswith("catalog@")

    CATALOG["gpt-image-2.5-flare"]["output_cost_per_image_token"] = 4e-05
    second = pricing.update(lib)
    assert {"model": "openai/gpt-image-2.5-flare", "field": "output_image", "before": 35.0, "after": 40.0} in second["changes"]
    CATALOG["gpt-image-2.5-flare"]["output_cost_per_image_token"] = 3.5e-05

    pricing.set_override(lib, "openai", "gpt-image-2.5-flare", "output_image", 25)
    assert pricing.rates_for(lib, "openai", "gpt-image-2.5-flare") == (
        {"input_text": 5.0, "input_image": 8.0, "output_image": 25.0}, "override")
    assert pricing.clear_override(lib, "openai", "gpt-image-2.5-flare") == 1
    with pytest.raises(InvalidInputError):
        pricing.set_override(lib, "openai", "x", "bogus", 1)
    assert pricing.rates_for(lib, "gemini", "models/gemini-3.1-flash-image-preview")[0]["output_image"] == 60.0


def test_auto_refresh_rules(lib, online, monkeypatch):
    assert pricing.ensure_fresh(lib) is None and not online  # tests run offline by default
    monkeypatch.delenv(pricing.OFFLINE_ENV)
    assert pricing.ensure_fresh(lib)["models"] == 2
    assert pricing.ensure_fresh(lib) is None and len(online) == 1  # fresh enough
    settings_store.set_value(lib, "pricing.max_age_days", 0)
    assert pricing.ensure_fresh(lib) is not None
    settings_store.set_value(lib, "pricing.auto_update", "false")
    assert pricing.ensure_fresh(lib) is None and len(online) == 2

    def broken(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(pricing.urllib.request, "urlopen", broken)
    settings_store.set_value(lib, "pricing.auto_update", "true")
    assert pricing.ensure_fresh(lib) is None  # never fails generation
    with pytest.raises(StorageError):
        pricing.update(lib)


# ---------------------------------------------------------------------- metering


def test_normalize_usage_and_cost():
    openai_usage = {"input_tokens": 300, "output_tokens": 1056,
                    "input_tokens_details": {"text_tokens": 40, "image_tokens": 260}}  # fmt: skip
    tokens = metering.normalize_usage("openai", openai_usage, 1)
    assert tokens == {"input_text_tokens": 40, "input_image_tokens": 260, "output_image_tokens": 1056, "output_text_tokens": 0}
    rates = {"input_text": 5.0, "input_image": 8.0, "output_image": 30.0}
    assert metering.cost_of(tokens, rates, images=1) == pytest.approx((40 * 5 + 260 * 8 + 1056 * 30) / 1e6)

    gemini_usage = {"prompt_token_count": 600, "candidates_token_count": 1300, "thoughts_token_count": 50,
                    "prompt_text_tokens": 40, "prompt_image_tokens": 560, "output_image_tokens": 1120,
                    "output_text_tokens": 180}  # fmt: skip
    tokens = metering.normalize_usage("gemini", gemini_usage, 1)
    assert tokens == {"input_text_tokens": 40, "input_image_tokens": 560, "output_image_tokens": 1120, "output_text_tokens": 230}
    plain = metering.normalize_usage("gemini", {"prompt_token_count": 95, "candidates_token_count": 1290}, 1)
    assert plain["output_image_tokens"] == 1290 and plain["input_text_tokens"] == 95
    assert metering.cost_of(dict.fromkeys(metering.TOKEN_FIELDS, 0), {"per_image": 0.045}, images=2) == 0.09
    assert metering.cost_of(dict.fromkeys(metering.TOKEN_FIELDS, 0), {}, images=2) is None


def test_gemini_adapter_reports_modality_tokens():
    from assetforge.image.providers.gemini import _add_usage

    usage: dict[str, int] = {}
    meta = SimpleNamespace(prompt_token_count=600, candidates_token_count=1120, thoughts_token_count=0,
                           total_token_count=1720,
                           prompt_tokens_details=[SimpleNamespace(modality=SimpleNamespace(value="TEXT"), token_count=40),
                                                  SimpleNamespace(modality=SimpleNamespace(value="IMAGE"), token_count=560)],
                           candidates_tokens_details=[SimpleNamespace(modality=SimpleNamespace(value="IMAGE"), token_count=1120)])  # fmt: skip
    _add_usage(usage, meta)
    _add_usage(usage, meta)
    assert usage["prompt_image_tokens"] == 1120 and usage["output_image_tokens"] == 2240 and usage["prompt_text_tokens"] == 80


class MeteredFake(FakeProvider):
    """A fake that reports Gemini-style usage for a real Gemini model name."""

    def default_model(self, operation):
        return "gemini-3.1-flash-image"

    def expected_output_tokens(self, call):
        return 1120

    def run(self, call):
        response = super().run(call)
        usage = {"prompt_token_count": 50 * call.n, "candidates_token_count": 1120 * call.n, "total_token_count": 1170 * call.n}
        return ProviderResponse(provider=self.name, model=call.model, images=response.images, usage=usage,
                                cost_usd=None, request=response.request)  # fmt: skip


def test_generation_records_tokens_cost_and_estimates(lib, monkeypatch):
    providers.OVERRIDES["gemini"] = MeteredFake(Settings({}), name="gemini", native_transparency=False)
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    first = generate_image(lib, ImageRequest(prompt="mossy rock", type="sprite", provider="gemini", project="panda-bamboo"),
                           Settings({}))  # fmt: skip
    [record] = reporting.records(lib, reporting.resolve_range("all"))
    expected = (50 * 0.5 + 1120 * 60) / 1e6
    assert record["output_image_tokens"] == 1120 and record["input_text_tokens"] == 50
    assert record["cost_usd"] == pytest.approx(expected) and first.cost_usd == pytest.approx(expected)
    assert record["project"] == "panda-bamboo" and record["price_source"] == "builtin" and record["job_id"] == first.job_id
    assert record["estimated_cost_usd"] == pytest.approx(1120 * 60 / 1e6, rel=0.05)  # expected tokens x price

    plan = generate_image(lib, ImageRequest(prompt="mossy rock 2", type="sprite", provider="gemini", n=2, dry_run=True),
                          Settings({}))  # fmt: skip
    assert plan.estimated_cost_usd == pytest.approx(2 * expected, rel=0.01)  # history: median actual per image
    from assetforge.core import jobs

    assert jobs.get_job(lib, first.job_id).cost_usd == pytest.approx(expected)


def test_backfill_from_saved_responses(lib, monkeypatch):
    providers.OVERRIDES["gemini"] = MeteredFake(Settings({}), name="gemini", native_transparency=False)
    monkeypatch.setattr(engine, "sleep", lambda s: None)
    result = generate_image(lib, ImageRequest(prompt="old call", type="sprite", provider="gemini"), Settings({}))
    lib.conn.execute("DELETE FROM usage_records")
    assert metering.backfill(lib) == 1 and metering.backfill(lib) == 0
    [record] = reporting.records(lib, reporting.resolve_range("all"))
    assert record["job_id"] == result.job_id and record["output_image_tokens"] == 1120


# ---------------------------------------------------------------------- reports


NOW = datetime(2026, 10, 19, 15, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("preset", "start", "end"),
    [("today", "2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z"),
     ("yesterday", "2026-10-18T00:00:00Z", "2026-10-19T00:00:00Z"),
     ("this-week", "2026-10-19T00:00:00Z", "2026-10-20T00:00:00Z"),
     ("last-7-days", "2026-10-13T00:00:00Z", "2026-10-20T00:00:00Z"),
     ("this-month", "2026-10-01T00:00:00Z", "2026-10-20T00:00:00Z"),
     ("last-month", "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z"),
     ("last-30-days", "2026-09-20T00:00:00Z", "2026-10-20T00:00:00Z"),
     ("all", None, "2026-10-20T00:00:00Z")],
)  # fmt: skip
def test_preset_ranges(preset, start, end):
    assert reporting.resolve_range(preset, now=NOW).utc_bounds() == (start, end)


def test_custom_ranges_and_timezones():
    rng = reporting.resolve_range(None, "2026-10-01", "2026-10-05", now=NOW)
    assert rng.utc_bounds() == ("2026-10-01T00:00:00Z", "2026-10-06T00:00:00Z")  # end date inclusive
    kolkata = reporting.resolve_range(None, "2026-10-01", "2026-10-01", tz_name="Asia/Kolkata", now=NOW)
    assert kolkata.utc_bounds() == ("2026-09-30T18:30:00Z", "2026-10-01T18:30:00Z")
    exact = reporting.resolve_range(None, "2026-10-01T10:00", None, now=NOW)
    assert exact.utc_bounds() == ("2026-10-01T10:00:00Z", "2026-10-19T15:30:00Z")
    for bad in ((None, "2026-10-05", "2026-10-01"), ("today", "2026-10-01", None), ("someday", None, None),
                (None, "19/10/2026", None)):  # fmt: skip
        with pytest.raises(InvalidInputError):
            reporting.resolve_range(*bad, now=NOW)
    with pytest.raises(InvalidInputError):
        reporting.resolve_range("today", tz_name="Mars/Olympus")


def _seed_usage(lib):
    rows = [("2026-10-01T09:00:00Z", "gemini", "gemini-3.1-flash-image", "panda-bamboo", 1120, 1),
            ("2026-10-01T23:30:00Z", "openai", "gpt-image-2.5-flare", "space-cats", 1056, 1),
            ("2026-10-03T12:00:00Z", "gemini", "gemini-3-pro-image", "panda-bamboo", 3360, 2),
            ("2026-10-10T12:00:00Z", "openai", "gpt-image-2.5-flare", None, 2112, 2)]  # fmt: skip
    for when, provider, model, project, out_tokens, images in rows:
        usage = ({"prompt_token_count": 100, "candidates_token_count": out_tokens} if provider == "gemini" else
                 {"input_tokens": 100, "output_tokens": out_tokens, "input_tokens_details": {"text_tokens": 100}})  # fmt: skip
        metering.record_call(lib, job_id=None, operation="generate", provider=provider, model=model, raw_usage=usage,
                             images=images, quality="medium", size="1K", estimated_cost=0.1, project=project,
                             created_at=when)  # fmt: skip


def test_report_aggregates_series_and_exports(lib):
    _seed_usage(lib)
    rng = reporting.resolve_range(None, "2026-10-01", "2026-10-05", now=NOW)
    report = reporting.build(lib, rng)
    t = report["totals"]
    assert (t["calls"], t["images"]) == (3, 4)
    assert t["output_image_tokens"] == 1120 + 1056 + 3360
    expected = (100 * 0.5 + 1120 * 60 + 100 * 5 + 1056 * 30 + 100 * 2 + 3360 * 120) / 1e6
    assert t["cost_usd"] == pytest.approx(expected, abs=1e-4)
    assert [p["period"] for p in report["series"]] == [f"2026-10-0{d}" for d in range(1, 6)]
    assert [p["calls"] for p in report["series"]] == [2, 0, 1, 0, 0]
    assert [r["key"] for r in report["by_provider"]] == ["gemini", "openai"]
    assert sum(r["share"] for r in report["by_model"]) == pytest.approx(1.0, abs=1e-3)
    assert {r["key"] for r in report["by_project"]} == {"panda-bamboo", "space-cats"}
    assert t["estimate_error_pct"] is not None

    in_kolkata = reporting.build(lib, reporting.resolve_range(None, "2026-10-02", "2026-10-02", tz_name="Asia/Kolkata",
                                                              now=NOW))  # fmt: skip
    assert in_kolkata["totals"]["calls"] == 1  # 23:30 UTC on the 1st is the 2nd in India

    weekly = reporting.build(lib, reporting.resolve_range("all", now=NOW), interval="week", provider="openai")
    assert weekly["totals"]["calls"] == 2 and all(p["period"].startswith("week of") for p in weekly["series"])
    assert weekly["range"]["filters"]["provider"] == "openai"

    csv_text = reporting.to_csv(reporting.records(lib, rng))
    assert csv_text.splitlines()[0].startswith("created_at,provider,model") and len(csv_text.splitlines()) == 4
    page = render(report)
    assert "<svg" in page and page.count('class="hit"') == 5 and "prefers-color-scheme: dark" in page
    with pytest.raises(InvalidInputError):
        reporting.build(lib, rng, interval="hour")


def test_cli_report_and_pricing(capsys, home, tmp_path, online, monkeypatch):
    from assetforge.core.catalog import Library

    with Library(home) as lib:
        _seed_usage(lib)
    code, out, err = run_cli(capsys, "report", "--from", "2026-10-01", "--to", "2026-10-10", "--html",
                             str(tmp_path / "r.html"), "--csv", str(tmp_path / "r.csv"), "--json")  # fmt: skip
    assert code == 0, err
    report = out["report"]
    assert report["totals"]["calls"] == 4 and (tmp_path / "r.html").is_file() and (tmp_path / "r.csv").is_file()
    assert report["outputs"] == {"html": str(tmp_path / "r.html"), "csv": str(tmp_path / "r.csv")}
    code, out, _ = run_cli(capsys, "report", "--range", "last-month", "--from", "2026-10-01", "--json")
    assert code == 4
    code, out, _ = run_cli(capsys, "report", "--range", "all", "--project", "space-cats", "--json")
    assert out["report"]["totals"]["calls"] == 1

    code, out, _ = run_cli(capsys, "pricing", "update", "--json")
    assert code == 0 and out["update"]["models"] == 2
    code, out, _ = run_cli(capsys, "pricing", "set", "gemini-3.1-flash-image", "output_image", "55", "--json")
    assert out["rates"]["output_image"] == 55 and out["source"] == "override"
    code, out, _ = run_cli(capsys, "pricing", "show", "--json")
    assert out["pricing"]["models"]["gemini/gemini-3.1-flash-image"]["rates"]["output_image"] == 55
    code, out, _ = run_cli(capsys, "pricing", "unset", "gemini-3.1-flash-image", "--json")
    assert out["removed"] == 1
    code, out, _ = run_cli(capsys, "pricing", "set", "gpt-image-2", "cheap", "1", "--json")
    assert code == 4

    monkeypatch.setenv("COLUMNS", "200")
    code, text, _ = run_cli(capsys, "report", "--range", "all")
    assert code == 0 and "Usage & cost" in text and "gemini" in text
    assert run_cli(capsys, "pricing", "show")[0] == 0
