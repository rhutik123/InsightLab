# InsightLab — Project Progress Log

## Completed Modules

- **Accounts (`accounts`):** User registration, login with open-redirect protection, POST-only logout, Django password validation, profile editing, password change, and per-user authorization isolation.
- **Datasets (`datasets`):** Multi-format upload & security validation across CSV (`.csv`), Excel (`.xlsx` via `openpyxl`), legacy Excel (`.xls` via `xlrd`), and JSON (`.json` via Python stdlib), worksheet selector (`/datasets/<pk>/select-sheet/`), JSON record-path selector + dot-notation object flattening (`/datasets/<pk>/select-json/`), normalized internal CSV representation preserving originals untouched, automatic column-type detection (`datetime`, `numeric`, `categorical`, `text`, `identifier`), flexible schema mapping (`SalesDataset.dataset_type` & `column_schema`), general dataset profiling, non-destructive preview-before-apply cleaning, searchable **Data Explorer** (`/datasets/explorer/`), and 3 synthetic sample CSV generators (`sales`, `traffic`, `expenses`).
- **Analytics (`analytics`):** General workspace **Dashboard** (Total Datasets, Total Records, Numerical Columns, Recent Analyses, dataset-aware charts), general **Data Analysis** (trends, correlations, segments, distributions, 7-day seasonal decomposition), interactive **Visualizations Studio** (`/analytics/visualizations/`), Z-score **Anomaly Detection** (time-series & cross-sectional modes), and quantitative **Decision Support** insights.
- **Forecasting & Research Lab (`forecasting`):** Chronological splitting, missing-date handling, leakage-free recursive lag/calendar features, 5 algorithms (Naive, Moving Average, Linear Regression, Random Forest, ARIMA), held-out metrics (MAE, RMSE, MAPE, R²), side-by-side **Model Comparison** (`/forecasting/compare/`), and all 6 MSc IT **Experiment Lab** research protocols.
- **Reports (`reports`):** Multi-section **InsightLab PDF Reports** generated via ReportLab and Matplotlib from real dataset profiles, cleaning logs, charts, anomalies, evaluations, and insights.

## General-Purpose vs. Sales-Specific Scope

- **General-purpose capabilities:** Multi-format upload/validation (`.csv`, `.xlsx`, `.xls`, `.json`, min 2 columns and 1 row), automatic schema detection across arbitrary column names, general profiling across all numeric/categorical columns, non-destructive cleaning across all columns, Data Explorer, custom Visualization Studio, cross-sectional & time-series Z-score anomaly detection, and forecasting on any mapped date column + numerical target column (verified across CSV, XLSX, XLS, and nested/multi-array JSON datasets).
- **Remaining sales-specific / compatibility details:** Internal Django app/model names (`SalesDataset`, `date_column`, `product_column`, `quantity_column`, `revenue_column`) are preserved intentionally for database and migration stability. The Experiment Lab's 6 academic protocols remain framed around the MSc IT sales forecasting research study while accepting any mapped dataset/target.

## Automated Test Status & Verification History

- **Latest verified automated suite (`manage.py test -v 2`):** **31 of 31 tests passed** (`0` failed, `0` skipped) in `22.952s` across `accounts` (6), `analytics` (3), `datasets` (15), `forecasting` (6), and `reports` (1).
  - *Test-count progression:* `17` initial SalesForecast tests &rarr; `20` generalized InsightLab tests (archived in `docs/*.pdf`) &rarr; `27` multi-format CSV/XLSX/XLS/JSON tests &rarr; `31` after targeted regression tests for Excel row/column limits, JSON key collisions, `>25` JSON collection warnings, and original-file preservation.
- **Technical checks:** `python -m compileall -q` passed cleanly; `manage.py check` reported `0` issues; `manage.py makemigrations --check --dry-run` reported `No changes detected`; `pip check` reported `No broken requirements found.`
- **Manual browser testing:** Complete manual browser QA was **not** repeated for this targeted bug-fix pass (earlier manual QA covered the initial multi-format launch prior to these targeted fixes).
- **Archived PDF documentation (`docs/`):** `docs/PROJECT_COMPLETE_DOCUMENTATION.pdf` and `docs/InsightLab_Manual_Testing_and_Quality_Assurance_Report.pdf` reflect the pre-extension 20-test CSV baseline and are retained as historical reference (marked outdated for multi-format updates).

## Targeted Corrections Completed (Latest Pass)

- **Excel Row & Column Limits (`datasets/data_processing.py`):** Replaced silent slicing/truncation at `200,000` rows / `250` columns in `.xlsx` and `.xls` extraction/validation with explicit `ExcelLimitExceededError` rejection explaining the exact limits while preserving uploaded workbooks untouched.
- **JSON Flattened-Column Collisions (`datasets/data_processing.py`):** Replaced flat dictionary overwrites with structural-path-aware flattening (`_flatten_json_record_items` + deterministic deduplication in `normalize_json_records`) so nested keys colliding with literal dotted keys (e.g. `{"metrics": {"visitors": 10}, "metrics.visitors": 99}`) preserve every value under unique column names with an ingestion note.
- **JSON >25 Collection Limit Notifications (`datasets/data_processing.py`, `datasets/views.py`, `templates/datasets/select_json.html`):** Preserved the `25`-collection discovery cap while detecting overflow (`inspect_json_structure`), surfacing a UI banner and Django warning, and persisting the notice in `dataset.ingestion_notes`.
- **Upload Empty-State Copy & Env Config Docs (`templates/datasets/dataset_list.html`, `.env.example`, `README.md`):** Updated `dataset_list.html` empty state to reflect `.csv`, `.xlsx`, `.xls`, and `.json`; replaced `.env` autoload/PostgreSQL placeholders in `.env.example` and `README.md` with OS environment instructions matching `config/settings.py`; replaced stale `sales_forecasting/` root label in `README.md`.
- **Dependencies (`requirements.txt`):** Replaced `wrapt==2.6.0.dev1` pre-release with stable `wrapt==1.17.3` (required by `formulaic`/`statsmodels` on Python 3.13+), converted `requirements.txt` from UTF-16 LE to UTF-8, and verified `openpyxl==3.1.5` & `xlrd==2.0.2`.

## Known Bugs & Technical Limitations

- **Unsupported formats:** Macro-enabled `.xlsm`, `.jsonl`, Parquet, XML, and encrypted/password-protected Excel workbooks are rejected at upload time.
- **Excel row/column & formula limits:** Excel sheets exceeding `200,000` rows or `250` columns are rejected cleanly rather than truncated; formula cells without cached values (`data_only=True`) import as missing with an ingestion warning.
- **JSON collection cap:** JSON discovery inspects up to `25` candidate arrays (max nesting depth `12`) and warns the user when additional collections are not inspected.
- **Deep-file CSV parse errors:** `validate_csv_upload` inspects the first 64 KB (`nrows=5`) at upload time; malformed syntax deeper in very large CSVs is caught during schema inspection/cleaning via view error handling.
- **Database engine:** SQLite (`db.sqlite3`) is the only active database configuration; PostgreSQL support is planned as future work.
- **Dataset-specific forecasting requirements:** Datasets without a date column cannot run time-series forecasting (gracefully blocked with an informative message), and very short series (`< 8` periods) fall back from ARIMA/ML lags to baseline behavior.
- **Statistical significance:** Experiment Lab reports empirical error metrics (`MAE`, `RMSE`, `MAPE`, `R²`) on chronological splits and does not perform formal hypothesis testing.

## Remaining / Optional Future Work

- Optional `.jsonl` and Parquet ingestion support.
- Optional XGBoost forecasting model and SARIMAX exogenous features.
- Formal statistical significance tests (Diebold–Mariano / Wilcoxon) for multi-dataset academic benchmarks.
- Production PostgreSQL + HTTPS deployment setup (`DJANGO_DEBUG=False`).
