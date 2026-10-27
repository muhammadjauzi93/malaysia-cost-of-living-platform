"""Registry of every data source in the platform."""

from __future__ import annotations

from col_pipeline.sources import cpi_state, fuel_prices, fx_rates
from col_pipeline.sources.base import QualityRules, Source, TableSpec

SOURCES: dict[str, Source] = {
    s.name: s for s in (fuel_prices.SOURCE, cpi_state.SOURCE, fx_rates.SOURCE)
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        known = ", ".join(sorted(SOURCES))
        raise KeyError(f"Unknown source {name!r}. Known sources: {known}") from None


__all__ = ["SOURCES", "QualityRules", "Source", "TableSpec", "get_source"]
