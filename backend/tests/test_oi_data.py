"""Offline tests for the NSE F&O Open Interest integration.

No network access is used: every test injects a downloader or calls the pure
parser/summarizer directly.
"""

from datetime import date

import pytest

from oi_data import (
    bhavcopy_url,
    clear_cache,
    fetch_open_interest,
    parse_fo_bhavcopy,
    summarize_open_interest,
)


HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,"
    "XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,"
    "LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
    "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
    "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)


def _row(
    trade_date: str = "2026-10-21",
    ticker: str = "RELIANCE",
    instrument_type: str = "STF",
    instrument_name: str = "RELIANCE26OCTFUT",
    expiry: str = "2026-10-29",
    strike: str = "",
    option_type: str = "",
    oi: str = "500000",
    oi_change: str = "5000",
    volume: str = "10000",
    close: str = "2500.00",
    last: str = "2501.00",
    prev_close: str = "2490.00",
    underlying: str = "2500.00",
) -> str:
    return ",".join(
        [
            trade_date, trade_date, "FO", "NSE", instrument_type, "999", "",
            ticker, "", expiry, expiry, strike, option_type, instrument_name,
            "2495.00", "2510.00", "2480.00", close, last, prev_close,
            underlying, close, oi, oi_change, volume, "25000000.00", "100",
            "F1", "250", "", "", "", "", "",
        ]
    )


def sample_csv(trade_date: str = "2026-10-21") -> str:
    return "\n".join(
        [
            HEADER,
            _row(trade_date=trade_date),
            _row(
                trade_date=trade_date,
                instrument_type="STO",
                instrument_name="RELIANCE26OCT2500CE",
                strike="2500.00",
                option_type="CE",
                oi="120000",
                oi_change="-2000",
                volume="5000",
                close="40.00",
            ),
            _row(
                trade_date=trade_date,
                instrument_type="STO",
                instrument_name="RELIANCE26OCT2500PE",
                strike="2500.00",
                option_type="PE",
                oi="180000",
                oi_change="3000",
                volume="7000",
                close="38.00",
            ),
            _row(
                trade_date=trade_date,
                ticker="INFY",
                instrument_name="INFY26OCTFUT",
                expiry="2026-10-29",
                oi="111111",
                oi_change="111",
                volume="222",
                close="1600.00",
                last="1601.00",
                prev_close="1590.00",
                underlying="1600.00",
            ),
        ]
    )


class TestParseBhavcopy:
    def test_parses_and_filters_symbol(self):
        contracts = parse_fo_bhavcopy(sample_csv(), symbol="RELIANCE")

        assert len(contracts) == 3
        assert all(c["symbol"] == "RELIANCE" for c in contracts)

    def test_filters_case_insensitively(self):
        contracts = parse_fo_bhavcopy(sample_csv(), symbol="reliance")
        assert len(contracts) == 3

    def test_normalizes_contract_fields(self):
        contracts = parse_fo_bhavcopy(sample_csv(), symbol="RELIANCE")
        future = next(c for c in contracts if c["option_type"] is None)

        assert future["instrument_type"] == "STF"
        assert future["open_interest"] == 500000
        assert future["change_in_open_interest"] == 5000
        assert future["volume"] == 10000
        assert future["close"] == 2500.0
        assert future["underlying_price"] == 2500.0
        assert future["strike"] is None
        assert future["expiry"] == "2026-10-29"
        assert future["trade_date"] == "2026-10-21"
        assert future["lot_size"] == 250

    def test_blank_and_invalid_numbers_become_none(self):
        csv_text = "\n".join(
            [
                HEADER,
                _row(oi="", oi_change="-", volume="not-a-number", close="", last=""),
            ]
        )
        contracts = parse_fo_bhavcopy(csv_text, symbol="RELIANCE")

        assert len(contracts) == 1
        assert contracts[0]["open_interest"] is None
        assert contracts[0]["change_in_open_interest"] is None
        assert contracts[0]["volume"] is None
        assert contracts[0]["close"] is None

    def test_empty_input_returns_empty_list(self):
        assert parse_fo_bhavcopy("") == []
        assert parse_fo_bhavcopy("   ") == []

    def test_no_filter_returns_all_tickers(self):
        contracts = parse_fo_bhavcopy(sample_csv())
        assert {c["symbol"] for c in contracts} == {"RELIANCE", "INFY"}


class TestSummarizeOpenInterest:
    def _summary(self):
        contracts = parse_fo_bhavcopy(sample_csv(), symbol="RELIANCE")
        return summarize_open_interest(contracts, "RELIANCE")

    def test_aggregates_real_reported_values(self):
        summary = self._summary()
        oi = summary["open_interest"]

        assert summary["available"] is True
        assert summary["symbol"] == "RELIANCE"
        assert summary["exchange"] == "NSE"
        assert summary["asOf"] == "2026-10-21"
        assert summary["contract_count"] == 3
        assert oi["total"] == 800000
        assert oi["futures"] == 500000
        assert oi["call_options"] == 120000
        assert oi["put_options"] == 180000
        assert oi["put_call_ratio"] == 1.5
        assert oi["change_in_open_interest_total"] == 6000
        assert oi["total_volume"] == 22000
        assert oi["underlying_price"] == 2500.0
        assert summary["expiries"] == ["2026-10-29"]
        assert summary["instrument_types"] == ["STF", "STO"]
        assert summary["source"].startswith("NSE India official F&O bhavcopy")
        assert "not proof of market manipulation" in summary["note"]
        assert "does not state a per-row unit" in summary["unit_note"]

    def test_contracts_are_ordered_and_complete(self):
        summary = self._summary()
        assert [c["option_type"] for c in summary["contracts"]] == [None, "CE", "PE"]

    def test_put_call_ratio_none_when_no_calls(self):
        csv_text = "\n".join([HEADER, _row(oi="500000")])
        contracts = parse_fo_bhavcopy(csv_text, symbol="RELIANCE")
        summary = summarize_open_interest(contracts, "RELIANCE")

        assert summary["open_interest"]["call_options"] == 0
        assert summary["open_interest"]["put_call_ratio"] is None

    def test_empty_contracts_are_honestly_unavailable(self):
        summary = summarize_open_interest([], "RELIANCE")

        assert summary["available"] is False
        assert summary["open_interest"] is None
        assert summary["contracts"] == []
        assert summary["contract_count"] == 0
        assert "RELIANCE" in summary["reason"]


class TestFetchOpenInterest:
    def setup_method(self):
        clear_cache()

    def test_success_with_explicit_date(self):
        downloaded = []

        def downloader(trade_date):
            downloaded.append(trade_date)
            return sample_csv(trade_date.isoformat())

        result = fetch_open_interest(
            "RELIANCE", as_of="2026-10-21", downloader=downloader
        )

        assert result["available"] is True
        assert result["asOf"] == "2026-10-21"
        assert result["contract_count"] == 3
        assert downloaded == [date(2026, 10, 21)]
        assert result["sourceUrl"].endswith(
            "BhavCopy_NSE_FO_0_0_0_20261021_F_0000.csv.zip"
        )
        assert result["fetchedAt"]
        assert result["attempted_dates"] == ["2026-10-21"]

    def test_falls_back_to_most_recent_published_date(self):
        downloaded = []

        def downloader(trade_date):
            downloaded.append(trade_date)
            if trade_date != date(2026, 10, 20):
                raise OSError("HTTP 404 not published")
            return sample_csv(trade_date.isoformat())

        result = fetch_open_interest(
            "RELIANCE", today=date(2026, 10, 22), downloader=downloader
        )

        assert result["available"] is True
        assert result["asOf"] == "2026-10-20"
        assert downloaded == [
            date(2026, 10, 22),
            date(2026, 10, 21),
            date(2026, 10, 20),
        ]

    def test_weekends_are_skipped(self):
        downloaded = []

        def downloader(trade_date):
            downloaded.append(trade_date)
            raise OSError("unavailable")

        result = fetch_open_interest(
            "RELIANCE", today=date(2026, 10, 25), downloader=downloader
        )

        # 2026-10-25 is Sunday, 10-24 is Saturday: both must be skipped.
        assert downloaded[0] == date(2026, 10, 23)
        assert all(day.weekday() < 5 for day in downloaded)
        assert result["available"] is False
        assert "Sunday" not in result["reason"]

    def test_symbol_missing_from_bhavcopy_is_unavailable(self):
        result = fetch_open_interest(
            "TCS",
            as_of="2026-10-21",
            downloader=lambda trade_date: sample_csv(trade_date.isoformat()),
        )

        assert result["available"] is False
        assert result["open_interest"] is None
        assert result["contracts"] == []
        assert "TCS" in result["reason"]
        assert "no contracts" in result["last_error"]

    def test_download_error_is_reported_not_faked(self):
        def boom(trade_date):
            raise RuntimeError("NSE archive returned HTTP 503")

        result = fetch_open_interest(
            "RELIANCE", as_of="2026-10-21", downloader=boom
        )

        assert result["available"] is False
        assert result["attempted_dates"] == ["2026-10-21"]
        assert "HTTP 503" in result["last_error"]
        assert "No NSE F&O bhavcopy" in result["reason"]

    def test_bse_is_unavailable_without_any_network_call(self):
        calls = []

        def downloader(trade_date):
            calls.append(trade_date)
            return sample_csv()

        result = fetch_open_interest(
            "RELIANCE", exchange="BSE", downloader=downloader
        )

        assert result["available"] is False
        assert calls == []
        assert "NSE derivatives only" in result["reason"]
        assert result["exchange"] == "BSE"

    def test_invalid_parameters_raise(self):
        with pytest.raises(ValueError, match="unsupported characters"):
            fetch_open_interest("", downloader=lambda d: sample_csv())
        with pytest.raises(ValueError, match="unsupported characters"):
            fetch_open_interest("BAD SYMBOL", downloader=lambda d: sample_csv())
        with pytest.raises(ValueError, match="exchange must be NSE or BSE"):
            fetch_open_interest("RELIANCE", exchange="LSE")
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            fetch_open_interest("RELIANCE", as_of="21-10-2026")
        with pytest.raises(ValueError, match="max_lookback_days"):
            fetch_open_interest("RELIANCE", max_lookback_days=True)
        with pytest.raises(ValueError, match="max_lookback_days"):
            fetch_open_interest("RELIANCE", max_lookback_days=31)

    def test_repeated_calls_with_injected_downloader_are_deterministic(self):
        result_one = fetch_open_interest(
            "RELIANCE",
            as_of="2026-10-21",
            downloader=lambda d: sample_csv(d.isoformat()),
        )
        result_two = fetch_open_interest(
            "RELIANCE",
            as_of="2026-10-21",
            downloader=lambda d: sample_csv(d.isoformat()),
        )

        assert result_one["open_interest"] == result_two["open_interest"]
        assert result_one["contracts"] == result_two["contracts"]
        assert result_one["asOf"] == result_two["asOf"]
