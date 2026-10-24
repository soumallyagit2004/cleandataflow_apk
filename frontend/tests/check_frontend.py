"""Static checks for the Stock Analysis page.

Run with: python frontend/tests/check_frontend.py
This intentionally uses only the Python standard library.
"""

from html.parser import HTMLParser
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.attributes = {}
        self.scripts = []
        self.stylesheets = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
            self.attributes[values["id"]] = values
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href"))
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


def main() -> int:
    html_path = ROOT / "stock-analysis.html"
    html = html_path.read_text(encoding="utf-8")
    parser = PageParser()
    parser.feed(html)

    required_ids = {
        "analysisForm",
        "symbolInput",
        "exchangeSelect",
        "windowSelect",
        "windowStatus",
        "analyzeBtn",
        "analysisStatus",
        "selectionNote",
        "metaPanel",
        "metaList",
        "metaBadge",
        "chartPanel",
        "chartLegend",
        "chartCanvasWrap",
        "priceChart",
        "anomalyMarkerTooltip",
        "chartNote",
        "anomalyLegend",
        "indicatorPanel",
        "indicatorGrid",
        "anomalyPanel",
        "anomalyGrid",
        "modeCandles",
        "modeLine",
        "volumeToggle",
        "levelsToggle",
        "resetZoom",
    }
    missing = required_ids - parser.ids
    assert not missing, f"missing element ids: {sorted(missing)}"

    expected_scripts = [
        "config.js",
        "script.js",
        "vendor/lightweight-charts.standalone.production.js",
        "chart-utils.js",
        "analysis.js",
    ]
    script_paths = [src.split("?", 1)[0] for src in parser.scripts]
    assert script_paths == expected_scripts, parser.scripts
    for src in script_paths:
        assert (ROOT / src).is_file(), f"missing script file: {src}"

    stylesheet_paths = [href.split("?", 1)[0] for href in parser.stylesheets if href]
    assert "style.css" in stylesheet_paths, parser.stylesheets
    for href in ("index.html", "stocks.html", "stock-analysis.html"):
        assert href in parser.links, f"navigation link missing: {href}"

    # The old page had a separate #volumeChart; the new page aligns volume on
    # the same chart canvas.
    assert "volumeChart" not in parser.ids

    vendor = (ROOT / "vendor" / "lightweight-charts.standalone.production.js").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "v4.2.3" in vendor, "unexpected Lightweight Charts version"
    assert "Apache" in vendor or "Apache" in (
        ROOT / "vendor" / "LIGHTWEIGHT-CHARTS-LICENSE"
    ).read_text(encoding="utf-8")

    analysis = (ROOT / "analysis.js").read_text(encoding="utf-8")
    utils = (ROOT / "chart-utils.js").read_text(encoding="utf-8")

    for symbol in (
        "addCandlestickSeries",
        "addHistogramSeries",
        "setMarkers",
        "createPriceLine",
        "subscribeCrosshairMove",
        "subscribeClick",
        "ResizeObserver",
        "RequestGuard",
        "buildAnomalyMarkers",
        "markerClusterAtTime",
        "mapBars",
        "renderAnalysis",
        "renderAnomalyLegend",
        "runAnalysis",
        "timeframesForInterval",
        "windowForId",
        "selectionFromParams",
        "syncUrl",
        "markerTooltipHtml",
        "setWindowStatus",
        "activeRequestKey",
        "preserveResults",
        "historical_anomalies",
    ):
        haystack = (
            analysis
            if symbol
            not in {
                "buildAnomalyMarkers",
                "markerClusterAtTime",
                "mapBars",
                "timeframesForInterval",
                "selectionFromParams",
            }
            else utils
        )
        assert symbol in haystack, f"missing implementation marker: {symbol}"

    for key in (
        "sma",
        "rsi",
        "fibonacci",
        "ema_deviation",
        "volume_price_divergence",
        "volume_spike",
        "volatility_anomaly",
        "bollinger_bands",
    ):
        assert key in analysis, f"indicator key not referenced: {key}"

    for key in (
        "lof_anomaly",
        "transformer_anomaly",
    ):
        assert key in analysis, f"detector key not referenced: {key}"
        assert key in utils, f"detector key missing from chart-utils: {key}"

    for source, name in ((analysis, "analysis.js"), (utils, "chart-utils.js")):
        assert "Math.random" not in source, f"{name} must not generate data"
        lowered = source.lower()
        assert "dummy" not in lowered, f"{name} must not contain dummy fallbacks"
        assert "sample price" not in lowered, f"{name} must not contain sample prices"

    # Provider-incompatible pairs are reconciled before the request, and the
    # single Historical Window select maps each option to a supported pair.
    assert "reconcileSelection" in utils
    assert "windowFromPeriod" in utils
    assert "HISTORICAL_WINDOWS" in utils
    assert "windowLabel" in utils
    assert "AbortController" in utils
    assert "isCurrent" in utils

    # Symbol and exchange are deliberately visible in the redesign. The single
    # Historical Window selector lives inside the chart header and controls
    # both the chart data and the analysis data from one guarded fetch.
    assert 'class="search-fields"' in html
    assert "advanced-fields" not in html
    for field_id in ("exchangeSelect", "windowSelect"):
        attrs = parser.attributes.get(field_id, {})
        assert field_id in parser.ids, f"missing control: {field_id}"
        assert attrs.get("tabindex") != "-1", f"{field_id} must be keyboard reachable"
        assert "hidden" not in attrs, f"{field_id} must be visible"

    # The period and interval selects were removed from the analysis form.
    assert "periodSelect" not in parser.ids
    assert "intervalSelect" not in parser.ids
    assert "timeframePanel" not in parser.ids
    assert 'data-tooltip="How much history to request' not in html
    assert 'data-tooltip="Bar size' not in html

    assert 'id="dashboardStage" class="dashboard-stage hidden"' in html
    assert html.index('id="windowSelect"') > html.index('id="chartPanel"')
    assert html.index('id="windowSelect"') < html.index('id="priceChart"')
    assert "windowSelect.addEventListener" in analysis
    assert "runAnalysis(nextSelection, { preserveResults: true, force: true })" in analysis

    # The published history list is exactly the honest provider-supported set.
    for period in ('"1d"', '"5d"', '"1mo"', '"3mo"', '"6mo"', '"1y"', '"5y"'):
        assert period in utils, f"timeframe period missing: {period}"
    assert "TIMEFRAME_OPTIONS" in utils
    assert "timeframesForInterval" in utils

    # Historical markers are clustered red circles, not per-detector arrows.
    assert 'color: "#ef4444"' in utils
    assert 'shape: "circle"' in utils
    assert "labelsSuppressed" in utils
    assert "pointCount" in utils
    assert "latestOnly" in utils
    assert "latest_only" in utils
    assert "historical_available" in utils
    assert "markerTooltipHtml" in analysis
    assert "pinnedAnomalyTime" in analysis
    assert "Historical + latest" in analysis

    # Detector limitations are visible instead of implying historical scores
    # that the backend deliberately does not return.
    assert "Latest-only" in analysis
    assert "no historical series" in analysis
    assert "not proof" in html.lower()

    style = (ROOT / "style.css").read_text(encoding="utf-8")
    for selector in (
        ".window-field",
        ".window-label",
        ".window-status",
        ".chart-canvas-wrap",
        ".anomaly-marker-tooltip",
        ".marker-swatch",
        ".anomaly-overlay-legend",
    ):
        assert selector in style, f"missing stylesheet selector: {selector}"

    # A slow provider call must settle into a visible timeout error instead of
    # leaving the page stuck on "Loading" forever.
    assert "REQUEST_TIMEOUT_MS" in analysis
    assert "setTimeout" in analysis
    assert "clearTimeout" in analysis
    assert "timedOut" in analysis

    # ------------------------------------------------------------------
    # The wishlist / login / register pages and the accounts backend were
    # deliberately removed. Nothing that referenced them may come back.
    # ------------------------------------------------------------------
    config = (ROOT / "config.js").read_text(encoding="utf-8")

    # config.js sets window.CDF_API_BASE dynamically for production and dev.
    # Anything else (keys, tokens, secrets) would be a regression.
    extra_config_lines = [
        line
        for line in config.splitlines()
        if line.strip().startswith("window.")
        and "CDF_API_BASE" not in line
    ]
    assert not extra_config_lines, (
        f"config.js must only set CDF_API_BASE: {extra_config_lines}"
    )
    assert "window.CDF_API_BASE = " in config
    # Ensure production does not have a hardcoded unconditional localhost:8000 assignment
    assert 'window.CDF_API_BASE = "http://localhost:8000";' not in config, (
        "config.js must not unconditionally hardcode localhost:8000"
    )

    # HTML files must not contain hardcoded localhost in markup
    for html_file in ROOT.glob("*.html"):
        html_text = html_file.read_text(encoding="utf-8")
        assert "http://localhost:8000" not in html_text, (
            f"hardcoded localhost:8000 found in {html_file.name}"
        )


    # The removed pages must stay removed, and no surviving file may still
    # point at them or at the removed backend endpoints.
    for removed_page in ("wishlist.html", "login.html", "register.html"):
        assert not (ROOT / removed_page).is_file(), (
            f"removed page is back on disk: {removed_page}"
        )

    for name in (
        "index.html",
        "stocks.html",
        "stock-analysis.html",
        "config.js",
        "script.js",
        "analysis.js",
        "chart-utils.js",
    ):
        source = (ROOT / name).read_text(encoding="utf-8")
        if name == "script.js":
            assert "wishlist" not in source.lower(), (
                "script.js still mentions wishlist"
            )
        for stale in (
            "wishlist.html",
            "login.html",
            "register.html",
            "navAuthLink",
            "/api/auth/",
            "/api/watchlist",
            "fetchAuthJson",
            "initWishlistPage",
            "initAuthForms",
            "clean-data-wishlist",
        ):
            assert stale not in source, (
                f"{name} still references removed feature: {stale}"
            )

    print("frontend checks passed")
    print(f"  html ids: {len(parser.ids)}")
    print(f"  scripts: {', '.join(parser.scripts)}")
    print(f"  vendor bytes: {len(vendor.encode('utf-8'))}")
    print("  removed pages verified: none of wishlist/login/register exist")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"frontend check failed: {error}", file=sys.stderr)
        raise SystemExit(1)
