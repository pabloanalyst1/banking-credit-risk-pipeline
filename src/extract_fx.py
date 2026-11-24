"""Extract USD/GTQ exchange rates from Banco de Guatemala (Banguat).

Banguat publishes the official reference exchange rate through a public SOAP web service.
If the service can't be reached (no internet, timeout, format change), the pipeline falls
back to a simulated series so it never breaks. Every row records its `source`.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

BANGUAT_URL = "https://www.banguat.gob.gt/variables/ws/TipoCambio.asmx"
BANGUAT_NS = "http://www.banguat.gob.gt/variables/ws/"

SOAP_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <TipoCambioRango xmlns="{ns}">
      <fechainit>{start}</fechainit>
      <fechafin>{end}</fechafin>
    </TipoCambioRango>
  </soap:Body>
</soap:Envelope>"""


def build_envelope(start: date, end: date) -> str:
    """Banguat expects dates as dd/mm/yyyy."""
    return SOAP_TEMPLATE.format(ns=BANGUAT_NS, start=start.strftime("%d/%m/%Y"), end=end.strftime("%d/%m/%Y"))


def _local(tag: str) -> str:
    """Strip the XML namespace: '{ns}fecha' -> 'fecha'."""
    return tag.rsplit("}", 1)[-1].lower()


def parse_rango_response(xml_text: str) -> pd.DataFrame:
    """Parse a TipoCambioRango response into a DataFrame [rate_date, usd_gtq].

    Each <Var> element holds a <fecha> and a rate (<venta>, <compra> or <referencia>).
    The parser ignores namespaces so small format changes don't break it.
    """
    root = ET.fromstring(xml_text)
    rows = []
    for el in root.iter():
        if _local(el.tag) != "var":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in el}
        raw_rate = fields.get("referencia") or fields.get("venta") or fields.get("compra")
        if not fields.get("fecha") or not raw_rate:
            continue
        rows.append({
            "rate_date": datetime.strptime(fields["fecha"], "%d/%m/%Y").date(),
            "usd_gtq": float(raw_rate),
        })
    df = pd.DataFrame(rows, columns=["rate_date", "usd_gtq"])
    return df.drop_duplicates("rate_date").sort_values("rate_date").reset_index(drop=True)


def fetch_banguat(start: date, end: date, timeout: int = 30) -> pd.DataFrame:
    """Call the Banguat web service one year at a time and combine the results."""
    import requests

    frames = []
    for year in range(start.year, end.year + 1):
        chunk_start = max(start, date(year, 1, 1))
        chunk_end = min(end, date(year, 12, 31))
        response = requests.post(
            BANGUAT_URL,
            data=build_envelope(chunk_start, chunk_end).encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{BANGUAT_NS}TipoCambioRango"',
            },
            timeout=timeout,
        )
        response.raise_for_status()
        frames.append(parse_rango_response(response.text))

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        raise ValueError("Banguat returned no exchange-rate rows")
    return df


def fallback_series(start: date, end: date, seed: int = 7) -> pd.DataFrame:
    """Simulated daily USD/GTQ series: a mean-reverting walk around 7.75 (the quetzal is very stable)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    rates = np.empty(len(dates))
    level = 7.82
    for i in range(len(dates)):
        level += 0.02 * (7.74 - level) + rng.normal(0, 0.004)
        rates[i] = level
    return pd.DataFrame({"rate_date": dates.date, "usd_gtq": rates.round(5)})


def get_fx_rates(start: date, end: date, offline: bool = False) -> pd.DataFrame:
    """Return daily USD/GTQ rates with a `source` column ('banguat' or 'fallback')."""
    if not offline:
        try:
            df = fetch_banguat(start, end)
            df["source"] = "banguat"
            log.info("FX: %d daily rates downloaded from Banguat", len(df))
            return df
        except Exception as exc:  # network errors, HTTP errors, parsing errors
            log.warning("FX: Banguat unavailable (%s). Using fallback series.", exc)
    df = fallback_series(start, end)
    df["source"] = "fallback"
    log.info("FX: %d daily rates from fallback series", len(df))
    return df
