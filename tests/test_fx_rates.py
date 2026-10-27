from datetime import date

import pandas as pd
import pytest

from col_pipeline.errors import DataContractError
from col_pipeline.extract import BnmClient
from col_pipeline.quality import blocking_failures, evaluate_frame
from col_pipeline.sources import fx_rates
from col_pipeline.sources.fx_rates import OUTPUT_COLUMNS, TRACKED_CURRENCIES, to_frame


def test_flattens_nested_rate_objects(fx_records):
    df = to_frame(fx_records)
    assert list(df.columns) == OUTPUT_COLUMNS
    usd = df[df.currency == "USD"].iloc[0]
    assert usd["rate_date"] == date(2026, 10, 23)
    assert (usd["buying_rate"], usd["selling_rate"], usd["middle_rate"]) == (4.0735, 4.0755, 4.0745)
    assert usd["session_code"] == "1700"


def test_only_tracked_currencies_are_kept(fx_records):
    assert set(to_frame(fx_records)["currency"]) == set(TRACKED_CURRENCIES)


def test_jpy_keeps_its_quoting_unit_of_100(fx_records):
    # Raw stores the rate as BNM quotes it. Converting to "per 1 JPY" is a derivation (dbt).
    df = to_frame(fx_records)
    assert df[df.currency == "JPY"]["unit"].tolist() == [100]
    assert df[df.currency == "USD"]["unit"].tolist() == [1]


def test_a_partly_null_rate_row_is_kept_but_an_all_null_one_is_dropped(fx_records):
    fx_records[0]["rate"]["middle_rate"] = None
    kept = to_frame(fx_records)
    assert pd.isna(kept[kept.currency == "USD"]["middle_rate"].iloc[0])

    for key in ("buying_rate", "selling_rate", "middle_rate"):
        fx_records[0]["rate"][key] = None
    dropped = to_frame(fx_records)
    assert "USD" not in set(dropped["currency"])


def test_repeated_rows_collapse_to_one(fx_records):
    assert len(to_frame([*fx_records, fx_records[0]])) == len(to_frame(fx_records))


def test_unexpected_shape_points_the_reader_at_the_probe_command(fx_records):
    fx_records[0].pop("rate")
    with pytest.raises(DataContractError, match="probe-bnm"):
        to_frame(fx_records)


def test_response_with_no_tracked_currency_fails(bnm_payload):
    only_other = [
        {**i, "_session": "1700"} for i in bnm_payload["data"] if i["currency_code"] == "TWD"
    ]
    with pytest.raises(DataContractError, match="tracked currencies"):
        to_frame(only_other)


def test_empty_extract_fails():
    with pytest.raises(DataContractError, match="zero records"):
        to_frame([])


def test_clean_fixture_passes_the_quality_gate(fx_records):
    results = evaluate_frame(to_frame(fx_records), fx_rates.TABLE, fx_rates.RULES)
    assert blocking_failures(results) == []
    assert all(r.passed for r in results)


def test_buying_above_selling_blocks_the_load(fx_records):
    fx_records[0]["rate"]["buying_rate"] = 9.0
    results = evaluate_frame(to_frame(fx_records), fx_rates.TABLE, fx_rates.RULES)
    assert [r.name for r in blocking_failures(results)] == ["order:buying_rate<=selling_rate"]


def test_a_missing_currency_only_warns(fx_records):
    without_eur = [r for r in fx_records if r["currency_code"] != "EUR"]
    results = evaluate_frame(to_frame(without_eur), fx_rates.TABLE, fx_rates.RULES)
    assert blocking_failures(results) == []
    assert any(r.name == "complete:currency" and not r.passed for r in results)


# ---- fetching ----


def test_weekdays_between_skips_weekends():
    # Fri 2026-10-23 .. Tue 2026-10-27 (exclusive): Fri, Mon
    assert fx_rates.weekdays_between(date(2026, 10, 23), date(2026, 10, 27)) == [
        date(2026, 10, 23),
        date(2026, 10, 26),
    ]
    assert fx_rates.weekdays_between(date(2026, 10, 27), date(2026, 10, 23)) == []


@pytest.fixture
def fake_bnm(monkeypatch, bnm_payload):
    calls = {"latest": 0, "dates": []}

    def latest(self, session_code, quote="rm"):
        calls["latest"] += 1
        return bnm_payload["data"]

    def rates_on(self, currency, day, session_code, quote="rm"):
        calls["dates"].append((currency, day))
        if day.weekday() == 2:  # pretend Wednesday was a public holiday: BNM 404s
            return []
        return [
            {
                "currency_code": currency,
                "unit": 1,
                "rate": {"date": day.isoformat(), "middle_rate": 4.0},
            }
        ]

    monkeypatch.setattr(BnmClient, "latest_rates", latest)
    monkeypatch.setattr(BnmClient, "rates_on", rates_on)
    monkeypatch.setattr(fx_rates.time, "sleep", lambda s: None)
    return calls


def test_daily_run_makes_one_call_and_no_history_lookups(fake_bnm, settings):
    records = fx_rates.fetch(settings, since=None)
    assert fake_bnm["latest"] == 1 and fake_bnm["dates"] == []
    assert len(records) == 7 and {r["_session"] for r in records} == {"1700"}


def test_backfill_looks_up_each_weekday_for_each_tracked_currency(fake_bnm, settings):
    # latest rate date in the fixture is Fri 2026-10-23; since Mon 10-19 -> Mon..Thu are looked up
    records = fx_rates.fetch(settings, since=date(2026, 10, 19))
    looked_up_days = sorted({d for _, d in fake_bnm["dates"]})
    assert looked_up_days == [
        date(2026, 10, 19),
        date(2026, 10, 20),
        date(2026, 10, 21),
        date(2026, 10, 22),
    ]
    assert len(fake_bnm["dates"]) == 4 * len(TRACKED_CURRENCIES)
    # Wednesday 10-21 returned nothing and that is fine: 3 days x 5 currencies + 7 latest items
    assert len(records) == 7 + 3 * len(TRACKED_CURRENCIES)


def test_history_that_finds_nothing_warns_about_a_changed_endpoint(monkeypatch, caplog):
    monkeypatch.setattr(BnmClient, "rates_on", lambda self, *a, **k: [])
    client = BnmClient()
    with caplog.at_level("WARNING"):
        out = fx_rates.fetch_history(
            client, date(2026, 10, 19), date(2026, 10, 21), "1700", sleep=lambda s: None
        )
    assert out == []
    assert "probe-bnm" in caplog.text
