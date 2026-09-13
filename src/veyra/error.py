"""Shared exception types for Veyra's data pipeline.

Distinct error categories let callers distinguish data-quality problems
from provider/network failures and make the pipeline fail loudly rather
than silently swallow bad data.
"""

from __future__ import annotations


class VeyraError(Exception):
    """Base class for all Veyra-specific errors."""


class DataError(VeyraError):
    """Base class for data-quality problems."""


class DataNormalizationError(DataError):
    """Provider payload could not be normalised to canonical candles."""


class DataValidationError(DataError):
    """Candles failed structural or OHLC validation checks."""


class ProviderError(VeyraError):
    """Failure in the market-data provider layer."""


class RateLimitError(ProviderError):
    """The provider rate limit was hit; caller should back off."""


class ProviderRequestError(ProviderError):
    """The provider returned an unrecoverable error response."""