"""Testy logiki skryptu bez sieci: `python tests/test_fetch_dividends.py` (yfinance jest podmieniane atrapą)."""
import sys
import types
from datetime import date
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))      # atrapa, żeby import nie wymagał biblioteki
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_dividends as fd  # noqa: E402

TODAY = date(2026, 10, 19)
CO = fd.Company("O", "O", "Realty Income", "USA")


def monthly(last: date, n: int = 8, amount: float = 0.27):
    out, d = [], last
    for _ in range(n):
        out.append((d, amount))
        d = date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, min(d.day, 28))
    return out


def test_confirmed_from_calendar():
    cal = {"Ex-Dividend Date": date(2026, 10, 31), "Dividend Date": date(2026, 11, 14)}
    entry, reason = fd.build_entry(CO, TODAY, cal, monthly(date(2026, 9, 30)), 55.0, "USD")
    assert reason is None and entry["estimated"] is False
    assert (entry["ex_date"], entry["pay_date"]) == ("2026-10-31", "2026-11-14")
    assert entry["amount"] == 0.27 and entry["amount_source"] == "ostatnia wypłata" and entry["yield_pct"] is not None


def test_amount_announced_when_in_history():
    hist = monthly(date(2026, 9, 30)) + [(date(2026, 10, 31), 0.28)]
    entry, _ = fd.build_entry(CO, TODAY, {"Ex-Dividend Date": date(2026, 10, 31)}, hist, 55.0, "USD")
    assert entry["amount"] == 0.28 and entry["amount_source"] == "ogłoszona"


def test_ex_passed_but_payment_pending_is_kept():
    cal = {"Ex-Dividend Date": date(2026, 10, 15), "Dividend Date": date(2026, 10, 30)}
    entry, _ = fd.build_entry(CO, TODAY, cal, monthly(date(2026, 10, 15)), 55.0, "USD")
    assert entry["ex_date"] == "2026-10-15" and entry["pay_date"] == "2026-10-30"


def test_estimate_monthly_payer_without_calendar():
    entry, _ = fd.build_entry(CO, TODAY, {}, monthly(date(2026, 9, 30)), 55.0, "USD")
    assert entry["estimated"] is True and entry["pay_date"] is None
    assert date.fromisoformat(entry["ex_date"]) >= TODAY


def test_annual_payer_outside_horizon_goes_to_without():
    cdr = fd.Company("CDR", "CDR.WA", "CD Projekt", "GPW")
    hist = [(date(2023, 6, 15), 0.1), (date(2024, 6, 14), 0.1), (date(2025, 6, 17), 0.1), (date(2026, 6, 16), 0.1)]
    entry, reason = fd.build_entry(cdr, TODAY, {}, hist, 200.0, "PLN")
    assert entry is None and "2027-06" in reason


def test_annual_payer_inside_horizon_is_estimated():
    cdr = fd.Company("CDR", "CDR.WA", "CD Projekt", "GPW")
    hist = [(date(2024, 11, 5), 1.0), (date(2025, 11, 4), 1.0), (date(2026, 11, 3), 1.0)]
    today = date(2027, 9, 1)
    entry, _ = fd.build_entry(cdr, today, {}, hist, 100.0, "PLN")
    assert entry and entry["estimated"] and entry["ex_date"] == "2027-11-03"


def test_suspended_payer_is_not_listed():
    intc = fd.Company("INTC", "INTC", "Intel", "USA")
    hist = [(date(2024, 2, 7), 0.125), (date(2024, 5, 6), 0.125), (date(2024, 8, 7), 0.125)]
    entry, reason = fd.build_entry(intc, TODAY, {}, hist, 119.0, "USD")
    assert entry is None and "2024-08-07" in reason


def test_non_payer_and_unknown_symbol():
    assert fd.build_entry(CO, TODAY, {}, [], 10.0, "USD")[1] == "brak wypłat dywidendy"
    assert "sprawdź symbol" in fd.build_entry(CO, TODAY, {}, [], None, None)[1]


def test_as_date_handles_nat_and_strings():
    assert fd.as_date(None) is None and fd.as_date(float("nan")) is None
    assert fd.as_date("2026-10-31T00:00:00") == date(2026, 10, 31)


if __name__ == "__main__":
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    for t in tests:
        t()
        print("OK ", t.__name__)
    print(f"\nWszystkie testy przeszły ({len(tests)}).")
