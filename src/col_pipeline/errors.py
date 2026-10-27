"""Exceptions shared across the pipeline."""


class DataContractError(ValueError):
    """The data broke a structural assumption that downstream code relies on.

    Raised by a source's `to_frame`: missing columns, an empty result, duplicate keys.
    Plausibility problems (out-of-range values, too few rows) are reported by
    `quality.evaluate_frame` instead, so they can carry a severity.
    """


class QualityGateError(RuntimeError):
    """One or more ERROR-severity quality checks failed, so the load was blocked."""
