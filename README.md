# InsightLab — Intelligent Data Analytics & Predictive Intelligence Platform

**Tagline:** *Transform Data into Meaningful Insights.*

InsightLab is a web-based data analytics and predictive intelligence platform built with Django, Python, Tailwind CSS, Chart.js, Pandas, Scikit-learn, Statsmodels, Matplotlib, and ReportLab. It enables users to upload, validate, normalize, profile, clean, explore, visualize, and analyze structured **CSV (`.csv`)**, **Excel (`.xlsx`, `.xls`)**, and **JSON (`.json`)** datasets, detect statistical anomalies, train and compare time-series forecasting models, conduct reproducible ML experiments, and export analytical PDF reports.

---

## Academic Research Focus

While InsightLab is designed as a general-purpose analytical workspace supporting diverse structured datasets (sales, web traffic, business expenses, inventory, or operational metrics), it also serves as the experimental platform for the MSc IT final-year research study:

> **"An Experimental Study of Machine Learning and Statistical Techniques for Sales Forecasting and Business Decision Support."**

---

## Problem Statement

Analysts, small businesses, and researchers often rely on fragmented spreadsheets or complex data-science notebooks to inspect data quality, identify trends, spot anomalies, and evaluate forecasting algorithms. This leads to inconsistent preprocessing, accidental time-series data leakage, difficulty comparing statistical vs. machine learning baselines fairly, and limited reproducibility across experiments.

---

## Project Objectives

1. Support uploading, safe parsing, normalization, and automatic schema detection (`datetime`, `numeric`, `categorical`, `text`, `identifier`) for arbitrary structured `.csv`, `.xlsx`, `.xls`, and `.json` datasets.
2. Profile datasets automatically for completeness, duplicates, distributions, descriptive statistics, and extreme outliers.
3. Provide non-destructive, preview-before-apply data cleaning that preserves original uploaded files unchanged.
4. Deliver interactive Exploratory Data Analysis (EDA), custom visualizations, and statistical Z-score anomaly detection.
5. Implement chronological, leakage-free time-series forecasting across 5 statistical and machine learning algorithms.
6. Evaluate models consistently on held-out test splits using MAE, RMSE, MAPE (with safe zero-actual handling), and R².
7. Provide a reproducible Research Experiment Lab across 6 controlled academic protocols.
8. Generate downloadable PDF reports backed strictly by real dataset and experiment records.

---

## Main Features

- **Workspace & Authentication (`accounts`):** Registration, login with open-redirect protection, POST-only logout, Django password validation, profile updates, password changes, and strict per-user data ownership isolation.
- **Dataset Management & Multi-Format Ingestion (`datasets`):** Unified upload validation across CSV (`.csv`), Excel (`.xlsx`, `.xls`), and JSON (`.json`) up to 15 MB (extension, file signature/archive inspection, zip-bomb & encryption guards, no macro/formula execution), interactive worksheet selector for multi-sheet Excel workbooks, JSON path selector + dot-notation object flattening, automatic column-type detection, flexible role mapping, automated profiling, and 3 synthetic sample CSV downloads (`sales`, `traffic`, `expenses`).
- **Data Explorer & Non-Destructive Cleaning (`datasets`):** Searchable row/column explorer (`/datasets/explorer/`) plus preview-before-apply cleaning (`/datasets/cleaning/`) supporting deduplication, date validation, negative-value filtering, numeric/categorical missing-value strategies, and optional aggregation.
- **Exploratory Data Analysis (`analytics`):** Dynamic KPI summaries, daily/weekly/monthly/yearly trends (with sufficiency guards), segment breakdowns, Pearson correlations, distribution histograms, day-of-week patterns, and 7-day seasonal decomposition (`statsmodels`).
- **Visualization Studio (`analytics`):** Interactive custom Chart.js builder (`/analytics/visualizations/`) supporting Line, Bar, Distribution Histogram, and 2-variable Scatter plots across any uploaded dataset.
- **Statistical Anomaly Detection (`analytics`):** Z-score detection over rolling time-series baselines or cross-sectional records, explicitly labeled as statistical observations rather than confirmed root causes.
- **Predictive Forecasting & Model Comparison (`forecasting`):** Chronological train/test splitting, explicit missing-date handling (`zero_fill`, `ffill`, `interpolate`), leakage-free recursive multi-step forecasting, dedicated side-by-side Model Comparison view (`/forecasting/compare/`), and demand/planning outlook insights.
- **Research Experiment Lab (`forecasting`):** Reproducible, seed-stored experiment suites across 6 academic dimensions (`/forecasting/experiments/`).
- **PDF Report Generation (`reports`):** Multi-section downloadable InsightLab PDF reports built with ReportLab and Matplotlib (`/reports/`).

---

## Technology Stack

- **Backend:** Python 3.15, Django 6.1.3, Django ORM
- **Database:** SQLite (default development), PostgreSQL-compatible via Django migrations
- **Frontend:** Django Templates (HTML5), Tailwind CSS v4.3.5 (compiled locally via `pytailwindcss`), Vanilla JavaScript, Chart.js v4.4.7 (local UMD bundle)
- **Data Science & ML:** Pandas 3.1.1, NumPy 2.5.5, Scikit-learn 1.9.1, Statsmodels 0.15.0, SciPy 1.18.1, `openpyxl` 3.1.5 (`.xlsx`), `xlrd` 2.0.2 (`.xls`), stdlib `json`
- **Visualization & Reporting:** Matplotlib 3.11.2, ReportLab 5.1.1

---

## Supported File Formats

InsightLab normalizes Excel and JSON datasets into an internal standardized CSV representation while preserving every original uploaded file untouched (`original_file` / `normalized_file` / `cleaned_file`), allowing all downstream modules (profiling, cleaning, exploration, analytics, anomaly detection, forecasting, and PDF reporting) to operate on one shared pipeline:

| Format | Extension | Ingestion Library | Multi-Structure Handling &amp; Limits |
|---|---|---|---|
| **CSV** | `.csv` | Pandas (`utf-8-sig` / `latin-1`) | Direct tabular text ingestion; minimum 2 columns and 1 data row; up to 15 MB. |
| **Excel** | `.xlsx` | `openpyxl` (`read_only=True`, `data_only=True`) | Single usable sheet auto-selects; multi-sheet workbooks prompt sheet selection with preview. Reads cached formula values without executing macros. Limits: 15 MB upload, 50 MB uncompressed XML / 500 entries, **200,000 rows &amp; 250 columns** per sheet (oversized sheets are rejected cleanly rather than truncated). |
| **Legacy Excel** | `.xls` | `xlrd` | Single usable sheet auto-selects; multi-sheet workbooks prompt sheet selection; converts Excel serial dates automatically. Limits: 15 MB upload, **200,000 rows &amp; 250 columns** per sheet (oversized sheets are rejected cleanly). |
| **JSON** | `.json` | Python stdlib `json` | Supports arrays of objects, wrapper keys (`data`, `records`, `items`, `results`, `rows`), single objects (1-row table), and interactive multi-array selection. Flattens nested objects with dot notation (deduplicating nested-vs-dotted key collisions safely), serializes nested arrays as JSON text, and inspects up to **25 candidate collections** (depth &le; 12), warning explicitly when additional collections exist. |

> **Unsupported formats:** Macro-enabled Excel (`.xlsm`), JSON Lines (`.jsonl`), Parquet, and XML are rejected with clear validation errors. Encrypted or password-protected Excel workbooks and formula cells lacking cached values cannot be evaluated by the server.

---

## System Architecture

InsightLab follows a clean layered Django architecture where ML, profiling, cleaning, EDA, and PDF generation live in dedicated domain modules independent of HTTP views:

```text
Browser (Django Templates + Tailwind CSS + Chart.js + Vanilla JS)
   │
   ▼
Django Routing & Function-Based Views (accounts, datasets, analytics, forecasting, reports)
   │
   ▼
Domain & ML Modules (data_processing.py, analysis.py, decision_support.py,
                     preprocessing.py, forecasting_models.py, evaluation.py,
                     research.py, report_generator.py)
   │
   ▼
SQLite (Django ORM)  +  Local Media Storage (Original Uploads, Normalized CSVs, Cleaned CSVs, PDFs)
```

---

## Project Folder Structure

```text
insightlab/
├── manage.py
├── requirements.txt         # UTF-8 pinned dependencies (including openpyxl & xlrd)
├── .env.example             # Reference list of supported OS environment variables
├── .gitignore
├── README.md
├── PROJECT_PROGRESS.md
├── config/                  # Settings, root URLs, WSGI/ASGI
├── accounts/                # Auth forms, views, URLs, tests
├── datasets/                # SalesDataset & WorkspaceActivity models, multi-format ingestion, profiling, cleaning, explorer
├── analytics/               # Dashboard, general EDA, custom visualizations, anomaly detection, decision support
├── forecasting/             # ForecastExperiment model, chronological preprocessing, 5 models, metrics, Experiment Lab
├── reports/                 # GeneratedReport model, ReportLab + Matplotlib PDF builder
├── templates/               # Modular Django HTML templates (no frontend JS frameworks)
├── static/                  # Tailwind source/output CSS, vanilla app.js, local Chart.js bundle
└── docs/                    # Archived baseline PDF documentation (reflects pre-extension 20-test CSV snapshot)
```

---

## Implemented Forecasting Algorithms & Evaluation Metrics

1. **Naive Forecast (Baseline):** Repeats the final observed training value across the horizon.
2. **Moving Average (7-Period):** Recursive rolling mean over the trailing window.
3. **Linear Regression:** Scikit-learn `LinearRegression` trained on leakage-free lag (`1, 2, 3, 7` adapted to length), past rolling mean, and calendar features (`day_of_week`, `month`, `day_of_month`), predicting recursively step-by-step.
4. **Random Forest Regressor:** Scikit-learn `RandomForestRegressor` (`n_estimators=100`, `max_depth=8`, seeded) with recursive multi-step prediction.
5. **ARIMA:** Statsmodels `ARIMA` trying `(1,1,1)`, `(1,0,0)`, `(0,1,1)` with graceful fallback to Naive on constant or very short series (`< 8` points).

**Evaluation Metrics (on held-out chronological test data):**
- **MAE** (Mean Absolute Error)
- **RMSE** (Root Mean Squared Error)
- **MAPE** (Mean Absolute Percentage Error, safely excluding zero-actual points with an explicit note or returning undefined when all actuals are zero)
- **R²** (Coefficient of Determination, computed when test length $\ge 2$ and variance $> 0$)

---

## Research Experimentation Methodology (Experiment Lab)

All 6 academic protocols preserve chronological ordering and store random seeds for reproducibility:
1. **Algorithm Comparison:** Evaluates all 5 algorithms on the identical chronological split.
2. **Historical Dataset Size:** Evaluates trailing `25%`, `50%`, `75%`, and `100%` training windows against the same fixed test set.
3. **Seasonality Features:** Compares lag-only features vs. lag + calendar features on Linear Regression and Random Forest.
4. **Missing Data & Imputation:** Masks seeded `0%`, `10%`, `20%`, `30%` gaps in training data repaired with `zero_fill`, `ffill`, or `interpolate`, evaluated on uncorrupted test data.
5. **Sales / Metric Volatility:** Reports Coefficient of Variation (CV) and anomaly counts alongside forecasting errors across segments.
6. **Segment / Product-Wise Comparison:** Evaluates forecasting accuracy independently across up to 8 segments.

---

## Installation & Setup (Windows PowerShell)

1. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
2. **Install dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```
3. **Configure environment variables (OS / Terminal Session):**
   `config/settings.py` reads environment variables directly from the operating system process environment (`.env` files are **not** loaded automatically). See `.env.example` for supported keys:
   - Local development (`DEBUG=True` default): if `DJANGO_SECRET_KEY` is unset, `config/settings.py` generates a gitignored `.dev_secret_key` automatically.
   - Production (`DJANGO_DEBUG=False`): export the variables in your PowerShell session or hosting platform before launching Django:
     ```powershell
     $env:DJANGO_DEBUG = "False"
     $env:DJANGO_SECRET_KEY = "replace-with-a-long-random-secret-key"
     $env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
     ```
   SQLite (`db.sqlite3`) is used by default; PostgreSQL database settings are not yet wired into `config/settings.py`.
4. **Apply database migrations:**
   ```powershell
   python manage.py migrate
   ```
5. **Rebuild Tailwind CSS (if modifying templates or styles):**
   ```powershell
   .\.venv\Scripts\tailwindcss.exe -i static/src/input.css -o static/css/output.css --minify
   ```

---

## Running & Testing the Application

- **Start the development server:**
  ```powershell
  .\.venv\Scripts\python.exe manage.py runserver
  ```
  Visit `http://127.0.0.1:8000/`.
- **Run all automated tests (31 tests across all 5 apps):**
  ```powershell
  .\.venv\Scripts\python.exe manage.py test -v 2
  ```

---

## Usage & Sample Datasets

1. Register an account and sign in.
2. Open **My Datasets** and either upload your own `.csv`, `.xlsx`, `.xls`, or `.json` file (minimum 2 columns and 1 data row, up to 15 MB) or download one of the built-in synthetic test CSV datasets:
   - **Sample Sales CSV** (`/datasets/sample-csv/?type=sales`)
   - **Sample Website Traffic CSV** (`/datasets/sample-csv/?type=traffic`)
   - **Sample Business Expenses CSV** (`/datasets/sample-csv/?type=expenses`)
3. For multi-sheet Excel workbooks or multi-array JSON files, select the worksheet or JSON path from the live preview; then confirm the auto-detected column schema and explore your data in **Profile**, **Data Explorer**, **Data Cleaning**, **Data Analysis**, **Visualizations**, **Anomaly Detection**, **Forecasting**, **Experiment Lab**, **Model Comparison**, and **Reports**.

---

## Current Limitations

- Supports `.csv`, `.xlsx`, `.xls`, and `.json` files up to 15 MB (`.xlsm`, `.jsonl`, Parquet, XML, and encrypted/password-protected Excel workbooks are not supported).
- Excel worksheets are capped at **200,000 non-empty rows** and **250 columns** per sheet (larger sheets are rejected clearly rather than truncated). Formula cells are read strictly using cached values (`data_only=True`, no macro or formula execution); formulas saved without cached values import as missing with an ingestion warning.
- JSON collection discovery inspects up to **25 candidate record arrays** (nesting depth &le; 12) and warns explicitly when additional collections are not inspected.
- Time-series forecasting requires a mapped date/time column and at least 8 continuous periods for chronological train/test evaluation.
- Model comparison highlights empirical error differences on the tested split without claiming formal statistical significance across multiple datasets.
- Decision-support notes label capacity/inventory advice as planning suggestions because stock-on-hand and supplier lead-time tables are not part of dataset uploads.

## Future Enhancements

- Optional Parquet / JSON Lines (`.jsonl`) ingestion support.
- Optional gradient boosting models (e.g., XGBoost / LightGBM) and SARIMAX exogenous regressors.
- Formal statistical significance tests (e.g., Diebold–Mariano / Wilcoxon signed-rank) across benchmark suites.
- Production PostgreSQL configuration (`DATABASES`), Gunicorn/WhiteNoise static serving, and HTTPS deployment settings.
