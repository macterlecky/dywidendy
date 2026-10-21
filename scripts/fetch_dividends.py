#!/usr/bin/env python3
"""Pobiera kalendarz dywidend z Yahoo Finance (biblioteka yfinance) i zapisuje data/dividends.json.

Lokalnie:  pip install -r scripts/requirements.txt && python scripts/fetch_dividends.py
Kod wyjścia 1 oznacza, że Yahoo nie odpowiedziało dla większości spółek. Wtedy plik NIE jest
nadpisywany, więc strona nadal pokazuje ostatnie poprawne dane.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import yfinance as yf

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "dividends.json"
HORIZON_DAYS = 180        # jak daleko w przód pokazujemy daty oszacowane
REQUEST_PAUSE = 1.5       # pauza między spółkami (Yahoo ogranicza liczbę zapytań)
MAX_ATTEMPTS = 3
MAX_FAILED_RATIO = 0.5    # powyżej tego progu nie nadpisujemy pliku

T = TypeVar("T")


@dataclass(frozen=True)
class Company:
    ticker: str   # tak wyświetlamy na stronie
    symbol: str   # symbol w Yahoo Finance (GPW = .WA, Euronext Amsterdam = .AS)
    name: str
    market: str


COMPANIES: tuple[Company, ...] = (
    # GPW
    Company("CDR", "CDR.WA", "CD Projekt", "GPW"),
    Company("BLO", "BLO.WA", "Bloober Team", "GPW"),      # na GPW ticker to BLO, nie BLOP
    Company("XTB", "XTB.WA", "XTB", "GPW"),
    Company("PKN", "PKN.WA", "ORLEN", "GPW"),
    Company("PZU", "PZU.WA", "PZU", "GPW"),
    Company("WTN", "WTN.WA", "Wittchen", "GPW"),
    Company("RBW", "RBW.WA", "Rainbow Tours", "GPW"),
    Company("COG", "COG.WA", "Cognor Holding", "GPW"),    # na GPW ticker to COG, nie COGP
    Company("INPST", "INPST.AS", "InPost", "Euronext"),   # InPost jest notowany w Amsterdamie, nie na GPW
    # USA
    Company("INTC", "INTC", "Intel", "USA"),
    Company("TTWO", "TTWO", "Take-Two Interactive", "USA"),
    Company("NVDA", "NVDA", "NVIDIA", "USA"),
    Company("RKLB", "RKLB", "Rocket Lab", "USA"),
    Company("LUNR", "LUNR", "Intuitive Machines", "USA"),
    Company("AGNC", "AGNC", "AGNC Investment", "USA"),
    Company("ARR", "ARR", "ARMOUR Residential REIT", "USA"),
    Company("EPR", "EPR", "EPR Properties", "USA"),
    Company("LTC", "LTC", "LTC Properties", "USA"),
    Company("O", "O", "Realty Income", "USA"),
    Company("NNN", "NNN", "NNN REIT", "USA"),
)


# ---------------------------------------------------------------- pomocnicze

def as_date(value: Any) -> date | None:
    """Zamienia datę z Yahoo (date, datetime, Timestamp, tekst) na date albo None."""
    if value is None or value != value:          # None lub NaT/NaN
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def with_retries(call: Callable[[], T], attempts: int = MAX_ATTEMPTS) -> T:
    """Ponawia zapytanie z rosnącą pauzą (Yahoo potrafi chwilowo odpowiedzieć błędem 429)."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def infer_interval(ex_dates: list[date]) -> int | None:
    """Typowy odstęp między wypłatami w dniach: miesięczny, kwartalny, półroczny albo roczny."""
    recent = sorted(ex_dates)[-8:]
    if len(recent) < 2:
        return None
    median = statistics.median((b - a).days for a, b in zip(recent, recent[1:]))
    for target, low, high in ((30, 20, 40), (91, 75, 105), (182, 160, 205), (365, 330, 400)):
        if low <= median <= high:
            return target
    return None


def estimate_next_ex(ex_dates: list[date], today: date) -> date | None:
    """Szacuje następny dzień ex-dividend na podstawie rytmu dotychczasowych wypłat."""
    interval = infer_interval(ex_dates)
    if interval is None:
        return None
    last = max(ex_dates)
    if (today - last).days > interval * 1.5 + 30:      # spółka przestała płacić
        return None
    nxt = last + timedelta(days=interval)
    while nxt < today:
        nxt += timedelta(days=interval)
    return nxt


# ---------------------------------------------------------------- logika wpisu

def build_entry(
    company: Company,
    today: date,
    calendar: dict[str, Any],
    history: list[tuple[Any, Any]],
    price: float | None,
    currency: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Zwraca (wpis do tabeli, powód braku wpisu). Dokładnie jedno z nich jest None."""
    ex = as_date(calendar.get("Ex-Dividend Date"))
    pay = as_date(calendar.get("Dividend Date"))
    converted = ((as_date(d), v) for d, v in history)
    paid = sorted((d, float(v)) for d, v in converted if d and v)
    ex_dates = [d for d, _ in paid]
    last_amount = paid[-1][1] if paid else None
    ttm = sum(v for d, v in paid if today - timedelta(days=365) <= d <= today)

    if (ex and ex >= today) or (pay and pay >= today):
        known = dict(paid).get(ex)
        ex_date, pay_date, estimated = ex, pay, False
        amount = known if known is not None else last_amount
        source = "ogłoszona" if known is not None else "ostatnia wypłata"
    else:
        if not paid:
            if price is None:
                return None, "brak danych w Yahoo (sprawdź symbol)"
            return None, "brak wypłat dywidendy"
        est = estimate_next_ex(ex_dates, today)
        if est is None:
            return None, f"ostatnia dywidenda {ex_dates[-1].isoformat()}, brak regularnego rytmu lub wypłaty wstrzymane"
        if (est - today).days > HORIZON_DAYS:
            return None, f"termin nieogłoszony, następna wypłata ok. {est.isoformat()}"
        ex_date, pay_date, estimated = est, None, True
        amount, source = last_amount, "ostatnia wypłata"

    return {
        "ticker": company.ticker,
        "name": company.name,
        "market": company.market,
        "currency": currency,
        "ex_date": ex_date.isoformat() if ex_date else None,
        "pay_date": pay_date.isoformat() if pay_date else None,
        "amount": round(amount, 4) if amount is not None else None,
        "amount_source": source if amount is not None else None,
        "yield_pct": round(ttm / price * 100, 2) if price and ttm else None,
        "estimated": estimated,
    }, None


# ---------------------------------------------------------------- pobieranie

def fetch_company(company: Company) -> dict[str, Any]:
    """Pobiera z Yahoo kalendarz, historię wypłat, kurs i walutę jednej spółki."""
    ticker = yf.Ticker(company.symbol)
    history = list(with_retries(lambda: ticker.dividends).items())      # błąd tutaj = spółka nieudana
    try:
        raw = with_retries(lambda: ticker.calendar)
        calendar = raw if isinstance(raw, dict) else {}
    except Exception as exc:                                              # bez kalendarza zostają szacunki
        print(f"  ostrzeżenie: brak kalendarza {company.symbol}: {exc}", file=sys.stderr)
        calendar = {}
    price = currency = None
    try:
        fast = ticker.fast_info
        price, currency = fast.last_price, fast.currency
    except Exception:
        pass
    return {"calendar": calendar, "history": history, "price": price, "currency": currency}


def main() -> int:
    today = datetime.now(timezone.utc).date()
    entries: list[dict[str, Any]] = []
    without: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    for index, company in enumerate(COMPANIES):
        if index:
            time.sleep(REQUEST_PAUSE)
        print(f"{company.symbol}…", file=sys.stderr)
        try:
            entry, reason = build_entry(company, today, **fetch_company(company))
        except Exception as exc:
            errors.append({"ticker": company.ticker, "error": str(exc)[:200]})
            print(f"  BŁĄD: {exc}", file=sys.stderr)
            continue
        if entry:
            entries.append(entry)
        else:
            without.append({"ticker": company.ticker, "name": company.name, "reason": reason or ""})

    if len(errors) / len(COMPANIES) > MAX_FAILED_RATIO:
        print(f"Za dużo błędów ({len(errors)}/{len(COMPANIES)}), zostawiam poprzedni plik.", file=sys.stderr)
        return 1

    entries.sort(key=lambda e: (e["ex_date"] or e["pay_date"] or "9999-12-31", e["ticker"]))
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "Yahoo Finance (yfinance)",
            "horizon_days": HORIZON_DAYS,
            "checked": len(COMPANIES),
        },
        "items": entries,
        "without_dividend": without,
        "errors": errors,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUTPUT)                                                   # zapis atomowy
    print(f"Zapisano {OUTPUT} ({len(entries)} wpisów, {len(without)} bez dywidendy, {len(errors)} błędów).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
