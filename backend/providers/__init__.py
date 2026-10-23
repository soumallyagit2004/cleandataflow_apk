"""Provider adapters for Clean Data Flow market data.

Public surface used by the manager:
    providers.registry   -> ordered list of provider modules
Each module exposes: PROVIDER_KEY, has_credentials(), supports(request),
fetch_history(request) -> ProviderResult (raises the base.py error taxonomy).
"""

from __future__ import annotations

from providers import alphavantage, twelvedata, yahoo  # noqa: F401

# Stable routing order (priority + fallback order). The manager may rotate or
# re-weight this list per request, but the membership fixed here defines the
# provider universe.
registry = [yahoo, twelvedata, alphavantage]
