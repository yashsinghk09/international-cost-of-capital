"""
Project Cost of Capital: USD vs. Local Currency
------------------------------------------------
Streamlit app that compares a project's cost of equity, cost of debt and WACC
in U.S. dollars and in the currency of a selected country.

Data sources (fetched live, cached for 12 hours, refreshable from the sidebar):
  * Industry unlevered betas  - Aswath Damodaran, NYU Stern (betas.xls)
  * USD BBB (Baa2) spread      - Aswath Damodaran, NYU Stern (ratings.html)
  * Statutory corporate tax    - OECD Corporate Tax Statistics
  * 10-year government yields  - Trading Economics (tradingeconomics.com/bonds)
                                 (embedded snapshot used if the live scrape is blocked)
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
st.set_page_config(
    page_title="Cost of Capital: USD vs Local Currency",
    page_icon="📊",
    layout="wide",
)

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URLS = [
    "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html",
]
OECD_REPORT_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
# OECD Data Explorer (SDMX) - same Corporate Tax Statistics database, machine-readable
OECD_SDMX_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
TE_URL = "https://tradingeconomics.com/bonds"
# Snapshot of tradingeconomics.com/bonds (10Y yield %, quote date), used only if the
# live scrape is blocked. Re-scrape and replace to update.
TE_SNAPSHOT_DATE = "2026-11-16 10:36 CT"
TE_SNAPSHOT = {
    "united-states": (5.1420, "Nov/16"),
    "united-kingdom": (5.3643, "Nov/16"),
    "japan": (2.8880, "Nov/16"),
    "australia": (5.1190, "Nov/16"),
    "germany": (3.3827, "Nov/16 10:36"),
    "brazil": (15.3450, "Nov/16"),
    "russia": (16.7400, "Nov/13"),
    "india": (7.3180, "Nov/16"),
    "canada": (3.8130, "Nov/16"),
    "italy": (4.4570, "Nov/16"),
    "france": (4.9520, "Nov/16"),
    "south-africa": (8.7200, "Nov/16"),
    "china": (1.6331, "Nov/16"),
    "switzerland": (0.5800, "Nov/16"),
    "mexico": (9.3080, "Nov/13"),
    "netherlands": (3.4690, "Nov/16"),
    "new-zealand": (4.7740, "Nov/16"),
    "portugal": (3.8460, "Nov/16"),
    "south-korea": (3.9860, "Nov/16"),
    "spain": (3.9640, "Nov/16 10:36"),
    "greece": (4.2950, "Nov/16"),
    "turkey": (36.4750, "Nov/16"),
    "finland": (3.7600, "Nov/16"),
    "lithuania": (4.3390, "Nov/16"),
    "norway": (4.3910, "Nov/16"),
    "poland": (6.3360, "Nov/16"),
    "romania": (7.1600, "Nov/16"),
    "slovakia": (4.3000, "Nov/16"),
    "slovenia": (3.9390, "Nov/16"),
    "sweden": (3.1000, "Nov/16"),
    "hungary": (5.5250, "Nov/16"),
    "iceland": (6.8100, "Nov/16"),
    "ireland": (3.5210, "Nov/16"),
    "austria": (3.6220, "Nov/16 10:36"),
    "belgium": (4.3980, "Nov/16 10:36"),
    "croatia": (3.8668, "Nov/13"),
    "czech-republic": (5.2600, "Nov/16"),
    "denmark": (3.1710, "Nov/16"),
    "euro-area": (3.94, "Nov/13"),
    "peru": (6.2414, "Nov/13"),
    "chile": (6.0100, "Nov/13"),
    "colombia": (12.6980, "Nov/13"),
    "singapore": (2.3429, "Nov/16"),
    "taiwan": (1.8700, "Nov/16"),
    "thailand": (1.9830, "Nov/16"),
    "vietnam": (4.5730, "Nov/16"),
    "hong-kong": (3.9960, "Nov/16"),
    "indonesia": (7.6070, "Nov/16"),
    "israel": (4.1270, "Nov/16"),
    "malaysia": (3.8654, "Nov/16"),
    "pakistan": (12.6220, "Nov/16"),
    "philippines": (7.7030, "Nov/16"),
    "zambia": (14.7400, "Nov/13"),
    "kenya": (12.5000, "Nov/16"),
    "nigeria": (16.5960, "Nov/13"),
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # %
DEFAULT_DEBT = 30   # %
CACHE_TTL = 12 * 60 * 60  # seconds

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MONTH_RE = (
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(?:\d{1,2},\s*)?(20\d{2})"
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def http_get(url: str, timeout: int = 30) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


def to_float(text) -> float | None:
    """Parse '4.25', '4.25%', '1,234.5' -> float. Returns None if not numeric."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("%", "").replace("−", "-")
    try:
        return float(s)
    except ValueError:
        return None


_ALIASES = {
    "us": "united states",
    "usa": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "korea": "south korea",
    "republic of korea": "south korea",
    "korea republic of": "south korea",
    "czechia": "czech republic",
    "turkiye": "turkey",
    "slovak republic": "slovakia",
    "china peoples republic of": "china",
    "hong kong china": "hong kong",
    "russian federation": "russia",
    "viet nam": "vietnam",
    "cote divoire": "ivory coast",
    "macao china": "macau",
    "chinese taipei": "taiwan",
}


def canon(name: str) -> str:
    """Normalise a country name so OECD and Trading Economics names can be joined."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z ]+", " ", s).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return _ALIASES.get(s, s)


def pretty(name: str) -> str:
    return " ".join(w.capitalize() for w in name.split())


def result(ok, data, source, url, data_date="n/a", error=None):
    return {
        "ok": ok,
        "data": data,
        "source": source,
        "url": url,
        "data_date": data_date,
        "fetched_at": now_utc(),
        "error": error,
    }


# ----------------------------------------------------------------------------
# Fetchers (cached)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_betas() -> dict:
    """Damodaran U.S. industry unlevered betas."""
    try:
        content = http_get(BETAS_URL, timeout=60).content
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)

        # Prefer the "Industry Averages" sheet; otherwise any sheet with "Industry Name"
        sheet = None
        for name, df in sheets.items():
            if "industry average" in name.lower():
                sheet = df
                break
        if sheet is None:
            for df in sheets.values():
                if df.fillna("").astype(str).apply(lambda c: c.str.strip().eq("Industry Name")).any().any():
                    sheet = df
                    break
        if sheet is None:
            raise ValueError("No sheet with an 'Industry Name' header was found.")

        mask = sheet.fillna("").astype(str).apply(lambda c: c.str.strip().eq("Industry Name"))
        hdr_row = mask.any(axis=1).idxmax()
        hdr_col = mask.loc[hdr_row].idxmax()

        # Date stamp sits in the rows above the header (e.g. "Date updated: January 2026")
        data_date = "n/a"
        top = sheet.iloc[:hdr_row]
        for _, row in top.iterrows():
            vals = [str(v).strip() for v in row.tolist() if pd.notna(v) and str(v).strip()]
            joined = " ".join(vals)
            if "updated" in joined.lower() or re.search(MONTH_RE, joined):
                m = re.search(MONTH_RE, joined)
                if m:
                    data_date = m.group(0)
                    break
                # date may be stored as an Excel date in the next cell
                for v in vals:
                    try:
                        data_date = pd.to_datetime(v).strftime("%B %Y")
                        break
                    except (ValueError, TypeError):
                        continue
                if data_date != "n/a":
                    break

        header = [str(h).strip() for h in sheet.iloc[hdr_row].tolist()]
        df = sheet.iloc[hdr_row + 1:].copy()
        df.columns = header

        beta_col = next((h for h in header if h.lower() == "unlevered beta"), None)
        if beta_col is None:
            beta_col = next(
                h for h in header
                if "unlevered beta" in h.lower() and "cash" not in h.lower()
            )
        name_col = header[hdr_col]

        out = df[[name_col, beta_col]].rename(
            columns={name_col: "Industry", beta_col: "Unlevered beta"}
        )
        out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
        out["Industry"] = out["Industry"].astype(str).str.strip()
        out = out.dropna()
        out = out[(out["Industry"] != "") & (out["Industry"].str.lower() != "nan")]
        out = out.reset_index(drop=True)
        if out.empty:
            raise ValueError("Beta table parsed but is empty.")
        return result(True, out, "Aswath Damodaran, NYU Stern - U.S. industry betas",
                      BETAS_URL, data_date)
    except Exception as e:  # noqa: BLE001
        return result(False, None, "Aswath Damodaran, NYU Stern - U.S. industry betas",
                      BETAS_URL, error=str(e))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_bbb_spread() -> dict:
    """USD default spread for Baa2/BBB, large non-financial service firms."""
    last_err = None
    for url in RATINGS_URLS:
        try:
            html = http_get(url).text
            text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
            m = re.search(MONTH_RE, text)
            data_date = m.group(0) if m else "n/a"

            # The first table containing Baa2/BBB is the large non-financial firms table
            tables = pd.read_html(io.StringIO(html))
            for t in tables:
                flat = t.fillna("").astype(str)
                hit = flat.apply(lambda c: c.str.contains("Baa2/BBB", regex=False)).any(axis=1)
                if not hit.any():
                    continue
                row = flat[hit].iloc[0].tolist()
                # Spread is the last percentage-like value in the row
                cands = [to_float(v) for v in row if "%" in v or to_float(v) is not None]
                cands = [c for c in cands if c is not None]
                pct_vals = [to_float(v) for v in row if "%" in v]
                spread = (pct_vals or cands)[-1]
                if spread is None:
                    continue
                spread = spread / 100 if spread > 0.2 else spread  # handle 1.11 vs 0.0111
                return result(True, spread,
                              "Aswath Damodaran, NYU Stern - ratings, spreads & coverage "
                              "(Baa2/BBB, large non-financial firms)",
                              url, data_date)
            raise ValueError("Baa2/BBB row not found on page.")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    return result(False, None, "Aswath Damodaran, NYU Stern - ratings & spreads",
                  RATINGS_URLS[0], error=last_err)


def _parse_oecd_sdmx(csv_text: str) -> tuple[dict, str]:
    df = pd.read_csv(io.StringIO(csv_text))
    if "OBS_VALUE" not in df.columns or "TIME_PERIOD" not in df.columns:
        raise ValueError("Unexpected SDMX layout.")
    label_cols = [c for c in df.columns
                  if not pd.api.types.is_numeric_dtype(df[c]) and c != "OBS_VALUE"]
    labels = df[label_cols].fillna("").astype(str).agg(" | ".join, axis=1).str.lower()

    # Keep the combined (central + sub-central) statutory CIT rate
    sel = labels.str.contains("combined")
    sel &= ~labels.str.contains("targeted|adjusted|sub-central|subcentral|central government rate")
    if not sel.any():
        sel = labels.str.contains("combined")
    if not sel.any():
        raise ValueError("Combined statutory rate series not found in SDMX data.")
    d = df[sel].copy()

    area_col = "Reference area" if "Reference area" in d.columns else "REF_AREA"
    d["TIME_PERIOD"] = pd.to_numeric(d["TIME_PERIOD"], errors="coerce")
    d["OBS_VALUE"] = pd.to_numeric(d["OBS_VALUE"], errors="coerce")
    d = d.dropna(subset=["TIME_PERIOD", "OBS_VALUE"])
    d = d.sort_values("TIME_PERIOD").groupby(area_col).tail(1)

    rates = {}
    for _, r in d.iterrows():
        v = r["OBS_VALUE"]
        rates[canon(r[area_col])] = {
            "rate": v / 100 if v > 1 else v,
            "year": int(r["TIME_PERIOD"]),
            "name": str(r[area_col]),
        }
    latest = int(d["TIME_PERIOD"].max())
    return rates, f"Tax year {latest} (latest available per country)"


def _parse_oecd_report(html: str) -> tuple[dict, str]:
    tables = pd.read_html(io.StringIO(html))
    best = None
    for t in tables:
        if len(t) < 20:
            continue
        num_cols = [c for c in t.columns[1:] if pd.to_numeric(t[c], errors="coerce").notna().mean() > 0.7]
        if num_cols:
            best = (t, num_cols)
            break
    if best is None:
        raise ValueError("No statutory-rate table found in the report HTML.")
    t, num_cols = best
    col = num_cols[-1]
    rates = {}
    for _, r in t.iterrows():
        v = to_float(r[col])
        if v is None:
            continue
        rates[canon(r[t.columns[0]])] = {"rate": v / 100 if v > 1 else v,
                                          "year": str(col), "name": str(r[t.columns[0]])}
    return rates, f"Corporate Tax Statistics 2024 report, column '{col}'"


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_tax_rates() -> dict:
    """OECD combined statutory corporate income tax rates."""
    errors = []
    try:
        rates, date = _parse_oecd_sdmx(http_get(OECD_SDMX_URL, timeout=60).text)
        if rates:
            return result(True, rates,
                          "OECD Corporate Tax Statistics - combined statutory CIT rate "
                          "(OECD Data Explorer)", OECD_SDMX_URL, date)
    except Exception as e:  # noqa: BLE001
        errors.append(f"SDMX: {e}")
    try:
        rates, date = _parse_oecd_report(http_get(OECD_REPORT_URL).text)
        if rates:
            return result(True, rates,
                          "OECD Corporate Tax Statistics 2024 - statutory CIT rates",
                          OECD_REPORT_URL, date)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Report page: {e}")
    return result(False, {}, "OECD Corporate Tax Statistics", OECD_REPORT_URL,
                  error="; ".join(errors))


def _parse_te(html: str) -> dict:
    """Parse tradingeconomics.com/bonds: each row links to /<country>/government-bond-yield."""
    soup = BeautifulSoup(html, "lxml")
    yields = {}
    for a in soup.find_all("a", href=True):
        m = re.match(r"^/([a-z\-]+)/government-bond-yield/?$", a["href"])
        if not m:
            continue
        key = canon(m.group(1).replace("-", " "))
        if key in yields:
            continue
        tr = a.find_parent("tr")
        if tr is None:
            continue
        tds = tr.find_all("td")
        cells = [td.get_text(" ", strip=True) for td in tds]
        idx = next((i for i, td in enumerate(tds) if td.find("a", href=a["href"])), 0)
        y = next((to_float(c) for c in cells[idx + 1:] if to_float(c) is not None), None)
        if y is None:
            continue
        yields[key] = {"yield": y / 100, "date": cells[-1] if cells else "", "name": pretty(key)}
    if not yields:
        raise ValueError("No yield rows found (page layout changed or request blocked).")
    return yields


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_bond_yields() -> dict:
    """10Y government bond yields from tradingeconomics.com/bonds.

    Scrapes the page live. If Trading Economics blocks the request (common from
    cloud hosts), falls back to TE_SNAPSHOT: the same page scraped on
    TE_SNAPSHOT_DATE and embedded below.
    """
    src = "Trading Economics - 10Y government bond yields"
    try:
        return result(True, _parse_te(http_get(TE_URL).text), src, TE_URL,
                      "Quote date per country (live)")
    except Exception as e:  # noqa: BLE001
        snap = {canon(slug.replace("-", " ")): {"yield": y / 100, "date": d,
                                               "name": pretty(canon(slug.replace("-", " ")))}
                for slug, (y, d) in TE_SNAPSHOT.items()}
        return result(True, snap, f"{src} (embedded snapshot - live fetch blocked)", TE_URL,
                      f"Snapshot scraped {TE_SNAPSHOT_DATE}; quote date per country",
                      error=f"Live fetch failed, using embedded snapshot: {e}")

# ----------------------------------------------------------------------------
# Finance
# ----------------------------------------------------------------------------
def cost_of_capital(beta_u, tax, debt_share, erp, rf, spread) -> dict:
    """Bottom-up CAPM / WACC in one currency (all inputs as decimals)."""
    de = debt_share / (1 - debt_share)
    beta_l = beta_u * (1 + (1 - tax) * de)
    ke = rf + beta_l * erp
    kd_pre = rf + spread
    kd_post = kd_pre * (1 - tax)
    wacc = (1 - debt_share) * ke + debt_share * kd_post
    return {"D/E": de, "Levered beta": beta_l, "Risk-free rate": rf,
            "Cost of equity": ke, "BBB spread": spread,
            "Pre-tax cost of debt": kd_pre, "After-tax cost of debt": kd_post,
            "WACC": wacc}


def auto_foreign_spread(usd_spread, rf_us, rf_fx) -> float:
    """Local-currency spread consistent with the USD BBB cost of debt.

    Converting the USD pre-tax cost of debt with the interest-rate differential
    (international Fisher effect):
        Kd_local = (1 + rf_US + s_USD) * (1 + rf_local) / (1 + rf_US) - 1
    so  s_local = Kd_local - rf_local = s_USD * (1 + rf_local) / (1 + rf_US)
    """
    return usd_spread * (1 + rf_fx) / (1 + rf_us)


def pct(x, d=2):
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


# ----------------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------------
st.title("📊 Project Cost of Capital: USD vs. Local Currency")
st.caption(
    "Bottom-up beta, CAPM and WACC with the same tax rate, debt share and "
    "equity risk premium in both currencies."
)

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Refresh data", use_container_width=True,
                 help="Clear the cache and fetch all sources again"):
        st.cache_data.clear()
        st.rerun()

with st.spinner("Fetching betas, spreads, tax rates and bond yields..."):
    betas_r = fetch_betas()
    spread_r = fetch_bbb_spread()
    tax_r = fetch_tax_rates()
    yields_r = fetch_bond_yields()

betas_df = betas_r["data"] if betas_r["ok"] else pd.DataFrame(
    {"Industry": [DEFAULT_INDUSTRY], "Unlevered beta": [float("nan")]}
)
tax_rates: dict = tax_r["data"] or {}
yields: dict = yields_r["data"] or {}

failed = [r["source"] for r in (betas_r, spread_r, tax_r, yields_r) if not r["ok"]]
if "embedded snapshot" in yields_r["source"]:
    st.info(f"Trading Economics blocked the live request, so 10Y yields come from the "
            f"Trading Economics snapshot embedded in the app ({TE_SNAPSHOT_DATE}).")
if failed:
    st.warning(
        "Some sources could not be fetched; use the manual overrides in the sidebar "
        "for the missing inputs:\n\n- " + "\n- ".join(failed)
    )

# Country universe: Trading Economics countries (+ OECD countries if TE is unavailable)
country_keys = set(yields) | (set(tax_rates) if not yields else set())
country_keys.add("united states")
country_names = sorted({pretty(k) for k in country_keys})

# ----------------------------------------------------------------------------
# Sidebar: selections and overrides
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("Project settings")
    industries = betas_df["Industry"].tolist()
    ind_idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
    industry = st.selectbox("Industry (Damodaran U.S.)", industries, index=ind_idx)

    c_idx = country_names.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in country_names else 0
    country = st.selectbox("Target country", country_names, index=c_idx)
    ckey = canon(country)

    erp = st.slider("Equity risk premium (%)", 3.0, 12.0, DEFAULT_ERP, 0.1) / 100
    debt_share = st.slider("Target debt share D/(D+E) (%)", 0, 80, DEFAULT_DEBT, 1) / 100

    tax_choice = st.radio(
        "Project tax rate", ["U.S.", f"{country}"], index=0, horizontal=True,
        help="The same tax rate is applied in both currencies.",
    )

    # ---- fetched values ----
    f_beta = betas_df.loc[betas_df["Industry"] == industry, "Unlevered beta"]
    f_beta = None if f_beta.empty or pd.isna(f_beta.iloc[0]) else float(f_beta.iloc[0])
    f_tax_us = tax_rates.get("united states", {}).get("rate")
    f_tax_fx = tax_rates.get(ckey, {}).get("rate")
    f_spread = spread_r["data"] if spread_r["ok"] else None
    f_rf_us = yields.get("united states", {}).get("yield")
    f_rf_fx = yields.get(ckey, {}).get("yield")

    st.header("Manual overrides")
    st.caption("Switch on to replace a fetched value. Inputs are forced on when a fetch failed.")

    def override(label, fetched, default, key, step=0.05, fmt="%.2f", is_pct=True):
        forced = fetched is None
        on = st.toggle(f"Override {label}", value=forced, key=f"tg_{key}", disabled=forced)
        shown = (fetched if fetched is not None else default)
        shown_ui = shown * 100 if is_pct else shown
        if on:
            v = st.number_input(f"{label}{' (%)' if is_pct else ''}", value=float(round(shown_ui, 4)),
                                step=step, format=fmt, key=f"ni_{key}")
            return (v / 100 if is_pct else v), True
        st.caption(f"Fetched: **{pct(fetched) if is_pct else f'{fetched:.2f}'}**")
        return fetched, False

    beta_u, ov_beta = override("unlevered beta", f_beta, 1.0, f"beta_{industry}", step=0.01, is_pct=False)
    tax_us, ov_tax_us = override("U.S. corporate tax rate", f_tax_us, 0.25, "taxus")
    if ckey != "united states":
        tax_fx, ov_tax_fx = override(f"{country} corporate tax rate", f_tax_fx, 0.25, f"taxfx_{ckey}")
    else:
        tax_fx, ov_tax_fx = tax_us, ov_tax_us
    usd_spread, ov_spread = override("USD BBB spread", f_spread, 0.015, "spread", step=0.01)
    rf_us, ov_rf_us = override("U.S. 10Y yield", f_rf_us, 0.0425, "rfus", step=0.01)
    if ckey != "united states":
        rf_fx, ov_rf_fx = override(f"{country} 10Y yield", f_rf_fx, 0.0425, f"rffx_{ckey}", step=0.01)
    else:
        rf_fx, ov_rf_fx = rf_us, ov_rf_us

    auto_sp = auto_foreign_spread(usd_spread, rf_us, rf_fx)
    ov_fx_spread = st.toggle(f"Override {country} BBB spread", value=False, key=f"tg_fxspread_{ckey}")
    if ov_fx_spread:
        fx_spread = st.number_input(f"{country} BBB spread (%)", value=float(round(auto_sp * 100, 4)),
                                    step=0.01, format="%.2f", key=f"ni_fxspread_{ckey}") / 100
    else:
        fx_spread = auto_sp
        st.caption(f"Auto-calculated: **{pct(auto_sp)}** = USD spread × (1+r_local)/(1+r_US)")

tax = tax_us if tax_choice == "U.S." else tax_fx
tax_label = f"{'U.S.' if tax_choice == 'U.S.' else country} ({pct(tax)})"

# ----------------------------------------------------------------------------
# Calculations
# ----------------------------------------------------------------------------
usd = cost_of_capital(beta_u, tax, debt_share, erp, rf_us, usd_spread)
loc = cost_of_capital(beta_u, tax, debt_share, erp, rf_fx, fx_spread)
fisher_wacc = (1 + usd["WACC"]) * (1 + rf_fx) / (1 + rf_us) - 1

# ----------------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------------
st.subheader(f"{industry} · {country}")
st.caption(f"Tax rate used for both currencies: {tax_label} · ERP {pct(erp)} · Debt share {pct(debt_share, 0)}")

left, right = st.columns(2)
for col, title, r in ((left, "🇺🇸 USD", usd), (right, f"🌐 {country} (local currency)", loc)):
    with col:
        st.markdown(f"### {title}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cost of equity", pct(r["Cost of equity"]))
        m2.metric("After-tax cost of debt", pct(r["After-tax cost of debt"]))
        m3.metric("WACC", pct(r["WACC"]))

rows = ["Risk-free rate", "Levered beta", "Cost of equity", "BBB spread",
        "Pre-tax cost of debt", "After-tax cost of debt", "WACC"]
detail = pd.DataFrame({
    "Item": rows,
    "USD": [usd[k] for k in rows],
    f"{country} (local)": [loc[k] for k in rows],
})
detail["Difference (local − USD)"] = detail[f"{country} (local)"] - detail["USD"]


def fmt_row(row):
    f = (lambda v: f"{v:.3f}") if row["Item"] == "Levered beta" else (lambda v: pct(v))
    return pd.Series({c: (f(row[c]) if c != "Item" else row[c]) for c in row.index})


st.dataframe(detail.apply(fmt_row, axis=1), hide_index=True, use_container_width=True)

st.info(
    f"**Consistency check:** converting the USD WACC with the 10Y yield differential "
    f"(1 + WACC_USD) × (1 + r_{country}) / (1 + r_US) − 1 gives **{pct(fisher_wacc)}**, "
    f"vs. the bottom-up local WACC of **{pct(loc['WACC'])}**. The gap arises because the "
    f"ERP and beta are applied to a different risk-free base rather than compounded."
)

# ----------------------------------------------------------------------------
# Inputs & sources
# ----------------------------------------------------------------------------
st.subheader("Inputs and data sources")


def src_row(item, value, r, overridden, is_beta=False, date=None, source=None):
    return {
        "Input": item,
        "Value used": f"{value:.3f}" if is_beta else pct(value),
        "Origin": "Manual override" if overridden else ("Fetched" if r["ok"] else "Manual (fetch failed)"),
        "Source": source or r["source"],
        "Data date": date or r["data_date"],
        "Fetched at": r["fetched_at"],
        "URL": r["url"],
    }


tax_us_year = tax_rates.get("united states", {}).get("year")
tax_fx_year = tax_rates.get(ckey, {}).get("year")
src = [
    src_row(f"Unlevered beta - {industry}", beta_u, betas_r, ov_beta, is_beta=True),
    src_row("U.S. corporate tax rate", tax_us, tax_r, ov_tax_us,
            date=f"Tax year {tax_us_year}" if tax_us_year else None),
    src_row(f"{country} corporate tax rate", tax_fx, tax_r, ov_tax_fx,
            date=f"Tax year {tax_fx_year}" if tax_fx_year else None),
    src_row("USD BBB (Baa2) spread", usd_spread, spread_r, ov_spread),
    src_row("U.S. 10Y government yield", rf_us, yields_r, ov_rf_us,
            date=yields.get("united states", {}).get("date") or None),
    src_row(f"{country} 10Y government yield", rf_fx, yields_r, ov_rf_fx,
            date=yields.get(ckey, {}).get("date") or None),
]
src.append({
    "Input": f"{country} BBB spread",
    "Value used": pct(fx_spread),
    "Origin": "Manual override" if ov_fx_spread else "Auto-calculated",
    "Source": "USD BBB spread × (1 + r_local) / (1 + r_US)",
    "Data date": "-", "Fetched at": "-", "URL": "",
})
src.append({"Input": "Equity risk premium", "Value used": pct(erp), "Origin": "User input",
            "Source": "Slider", "Data date": "-", "Fetched at": "-", "URL": ""})
src.append({"Input": "Target debt share D/(D+E)", "Value used": pct(debt_share, 0),
            "Origin": "User input", "Source": "Slider", "Data date": "-",
            "Fetched at": "-", "URL": ""})
src_df = pd.DataFrame(src)
st.dataframe(src_df, hide_index=True, use_container_width=True,
             column_config={"URL": st.column_config.LinkColumn("URL", display_text="link")})

with st.expander("Fetch status and errors"):
    for r in (betas_r, spread_r, tax_r, yields_r):
        status = "✅" if r["ok"] else "❌"
        st.markdown(f"{status} **{r['source']}** - data date: {r['data_date']} - "
                    f"fetched {r['fetched_at']} - [source]({r['url']})")
        if r["error"]:
            st.code(r["error"])

# ----------------------------------------------------------------------------
# Methodology
# ----------------------------------------------------------------------------
with st.expander("📘 Methodology and main assumptions", expanded=False):
    st.markdown(f"""
**Formulas (applied separately in each currency)**

| Step | Formula |
|---|---|
| Debt-to-equity | D/E = d / (1 − d), with d = target debt share = {pct(debt_share, 0)} |
| Levered beta (Hamada) | βL = βU × [1 + (1 − t) × D/E] |
| Cost of equity (CAPM) | Ke = r_f + βL × ERP |
| Pre-tax cost of debt | Kd = r_f + BBB spread |
| After-tax cost of debt | Kd × (1 − t) |
| WACC | (1 − d) × Ke + d × Kd × (1 − t) |

**Main assumptions**

1. **Same business risk everywhere.** The project uses the U.S. industry unlevered beta from
   Damodaran (not corrected for cash) in both currencies.
2. **Same project tax rate, debt share and ERP in both currencies.** You choose whether the
   tax rate is the U.S. or the {country} combined statutory CIT rate (OECD).
3. **Risk-free rates** are 10-year government bond yields from Trading Economics. For countries
   with sovereign default risk the local yield also includes a default spread, so the local
   "risk-free" rate (and Ke) may be overstated; Damodaran would strip out the sovereign spread.
4. **USD cost of debt** = U.S. 10Y yield + the Baa2/BBB default spread for large non-financial
   firms (Damodaran ratings table), i.e. the project is assumed to borrow at a BBB rating.
5. **Local-currency spread (auto).** The local pre-tax cost of debt is the USD cost of debt
   converted with the interest-rate differential (international Fisher effect), which gives
   s_local = s_USD × (1 + r_local) / (1 + r_US). You can override it.
6. **Local cost of equity** applies the same ERP to the local risk-free rate. No separate
   country risk premium is added; raise the ERP if you want to include one.
7. Statutory (not effective or marginal-after-incentives) tax rates are used; interest is
   assumed fully tax-deductible.
""")

# ----------------------------------------------------------------------------
# All-industry comparison
# ----------------------------------------------------------------------------
st.subheader("All-industry comparison")

if betas_r["ok"]:
    allind = betas_df[~betas_df["Industry"].str.lower().str.startswith("total market")].copy()
    rows_all = []
    for _, r in allind.iterrows():
        u = cost_of_capital(r["Unlevered beta"], tax, debt_share, erp, rf_us, usd_spread)
        l = cost_of_capital(r["Unlevered beta"], tax, debt_share, erp, rf_fx, fx_spread)
        rows_all.append({
            "Industry": r["Industry"],
            "Unlevered beta": r["Unlevered beta"],
            "Levered beta": u["Levered beta"],
            "Cost of equity (USD)": u["Cost of equity"],
            f"Cost of equity ({country})": l["Cost of equity"],
            "After-tax Kd (USD)": u["After-tax cost of debt"],
            f"After-tax Kd ({country})": l["After-tax cost of debt"],
            "WACC (USD)": u["WACC"],
            f"WACC ({country})": l["WACC"],
        })
    all_df = pd.DataFrame(rows_all).sort_values("WACC (USD)", ascending=False).reset_index(drop=True)

    metric = st.radio("Chart metric", ["WACC", "Cost of equity"], horizontal=True)
    chart_df = all_df.melt(
        id_vars=["Industry"],
        value_vars=[f"{metric} (USD)", f"{metric} ({country})"],
        var_name="Currency", value_name="Value",
    )
    chart_df["Selected"] = chart_df["Industry"] == industry
    order = all_df.sort_values(f"{metric} (USD)", ascending=False)["Industry"].tolist()
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            y=alt.Y("Industry:N", sort=order, title=None,
                    axis=alt.Axis(labelLimit=260, labelFontSize=10)),
            x=alt.X("Value:Q", title=metric, axis=alt.Axis(format="%")),
            yOffset="Currency:N",
            color=alt.Color("Currency:N", legend=alt.Legend(orient="top", title=None)),
            opacity=alt.condition(alt.datum.Selected, alt.value(1.0), alt.value(0.55)),
            tooltip=["Industry", "Currency", alt.Tooltip("Value:Q", format=".2%")],
        )
        .properties(height=max(400, 18 * len(all_df)))
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption(f"The selected industry ({industry}) is shown at full opacity.")

    pct_cols = [c for c in all_df.columns if c not in ("Industry", "Unlevered beta", "Levered beta")]
    st.dataframe(
        all_df.style.format({**{c: "{:.2%}" for c in pct_cols},
                             "Unlevered beta": "{:.3f}", "Levered beta": "{:.3f}"}),
        hide_index=True, use_container_width=True, height=420,
    )
else:
    all_df = pd.DataFrame()
    st.info("The industry beta table could not be fetched, so the all-industry comparison is unavailable.")

# ----------------------------------------------------------------------------
# Downloads
# ----------------------------------------------------------------------------
st.subheader("Download results")

summary_df = detail.copy()
assump_df = pd.DataFrame({
    "Setting": ["Industry", "Target country", "Project tax rate", "Equity risk premium",
                "Debt share D/(D+E)", "Generated at"],
    "Value": [industry, country, tax_label, pct(erp), pct(debt_share, 0), now_utc()],
})

xbuf = io.BytesIO()
with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
    assump_df.to_excel(xw, sheet_name="Settings", index=False)
    summary_df.to_excel(xw, sheet_name="Results", index=False)
    src_df.to_excel(xw, sheet_name="Inputs & sources", index=False)
    if not all_df.empty:
        all_df.to_excel(xw, sheet_name="All industries", index=False)

slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{industry}_{country}").strip("_")
d1, d2, d3 = st.columns(3)
d1.download_button("⬇️ Excel workbook (all tables)", xbuf.getvalue(),
                   file_name=f"cost_of_capital_{slug}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   use_container_width=True)
d2.download_button("⬇️ Results CSV", summary_df.to_csv(index=False).encode(),
                   file_name=f"results_{slug}.csv", mime="text/csv", use_container_width=True)
d3.download_button("⬇️ All-industry CSV",
                   all_df.to_csv(index=False).encode() if not all_df.empty else b"",
                   file_name=f"all_industries_{slug}.csv", mime="text/csv",
                   disabled=all_df.empty, use_container_width=True)

st.caption(
    "Data: Aswath Damodaran (NYU Stern), OECD Corporate Tax Statistics, Trading Economics. "
    "Scraped data can change format without notice; check the sources table and use overrides "
    "when needed. For analysis only, not investment advice."
)
