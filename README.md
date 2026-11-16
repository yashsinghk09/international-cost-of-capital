# Project Cost of Capital: USD vs. Local Currency

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in U.S. dollars and in the currency of any selected country**. It uses live market and reference data, and you can override any input by hand.

## Features

- **Live data**, cached for 12 hours, with a **🔄 Refresh data** button that clears the cache and fetches everything again:

  | Input | Source | How it is retrieved |
  |---|---|---|
  | U.S. industry **unlevered beta** | [Aswath Damodaran – betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | Parses the *Industry Averages* sheet, column **Unlevered beta** |
  | **USD BBB spread** | [Damodaran – ratings.html](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | **Baa2/BBB** row of the large non-financial firms table |
  | **Corporate tax rates** | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) | Combined statutory CIT rate, latest year, from the OECD Data Explorer (SDMX) feed of the same database; falls back to the report page |
  | **10Y government bond yields** | [Trading Economics – Bonds](https://tradingeconomics.com/bonds) | Scrapes the page, no API key needed |

- A **sources table** lists every value used, where it came from (fetched, manual override or auto-calculated), the source, the data date and the fetch time.
- **Settings:** industry, target country, equity risk premium and target debt share.
- **Manual overrides:** unlevered beta, U.S. and foreign tax rates, USD BBB spread, U.S. and foreign 10Y yields, and the foreign BBB spread. When the foreign-spread override is off, the app shows the auto-calculated value.
- **Tax switch:** apply either the U.S. or the selected country's tax rate in both currencies.
- **Side-by-side results**, a **methodology and assumptions** panel, an **all-industry comparison** (chart and table), and **downloads** (an Excel workbook with every table, plus CSVs).

### Defaults on launch
Software (System & Application) · United States · U.S. tax rate · 6.5% ERP · 30% debt.

## Methodology

Each currency is calculated with its own risk-free rate. Beta, tax rate, debt share and ERP are the same in both:

```
D/E            = d / (1 − d)
Levered beta   = βu × [1 + (1 − t) × D/E]                 (Hamada)
Cost of equity = r_f + βL × ERP                            (CAPM)
Pre-tax Kd     = r_f + BBB spread
After-tax Kd   = Kd × (1 − t)
WACC           = (1 − d) × Ke + d × Kd × (1 − t)
```

**Auto foreign BBB spread.** The USD pre-tax cost of debt is converted to local currency with the interest-rate differential (international Fisher effect):

```
Kd_local = (1 + r_US + s_USD) × (1 + r_local) / (1 + r_US) − 1
⇒ s_local = s_USD × (1 + r_local) / (1 + r_US)
```

The app also shows a consistency check: the USD WACC converted with the same differential, next to the bottom-up local WACC.

### Main assumptions and caveats
1. Business risk (the U.S. industry unlevered beta) is the same in every country.
2. Local 10Y yields may include sovereign default risk, so in riskier countries the local "risk-free" rate and cost of equity may be overstated.
3. No separate country risk premium is added. Raise the ERP if you want one.
4. Tax rates are statutory (combined central plus sub-central) rates, and interest is assumed fully deductible.
5. **Scraping is fragile.** If a site changes its layout or blocks the Streamlit Cloud server, the app shows a warning, turns on the manual input for the missing value, and records the error under *Fetch status and errors*.

## Run locally

```bash
git clone https://github.com/<your-user>/<your-repo>.git
cd <your-repo>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push `app.py`, `requirements.txt` and `README.md` to a GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io), click **Create app**, then pick the repo, the branch and `app.py`.
3. Click **Deploy**. No secrets or API keys are needed.

## Files

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app: data fetchers, calculations and UI |
| `requirements.txt` | Python dependencies (`xlrd` reads the `.xls` beta file) |
| `README.md` | This document |

## Disclaimer
For educational and analytical use only. This is not investment advice. Data belongs to its respective providers. Check their terms of use before using it commercially.
