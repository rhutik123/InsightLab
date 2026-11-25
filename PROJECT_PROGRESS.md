# InsightLab — Project Progress Log

## Completed Modules

- **Accounts (`accounts`):** User registration, login with open-redirect protection, POST-only logout, Django password validation, profile editing, password change, and per-user authorization isolation.
- **Datasets (`datasets`):** CSV upload validation, automatic column-type detection (`datetime`, `numeric`, `categorical`, `text`, `identifier`), flexible schema mapping (`SalesDataset.dataset_type` & `column_schema`), general dataset profiling, non-destructive preview-before-apply cleaning, searchable **Data Explorer** (`/datasets/explorer/`), and 3 synthetic sample CSV generators (`sales`, `traffic`, `expenses`).
- **Analytics (`analytics`):** General workspace **Dashboard** (Total Datasets, Total Records, Numerical Columns, Recent Analyses, dataset-aware charts), general **Data Analysis** (trends, correlations, segments, distributions, 7-day seasonal decomposition), interactive **Visualizations Studio** (`/analytics/visualizations/`), Z-score **Anomaly Detection** (time-series & cross-sectional modes), and quantitative **Decision Support** insights.
- **Forecasting & Research Lab (`forecasting`):** Chronological splitting, missing-date handling, leakage-free recursive lag/calendar features, 5 algorithms (Naive, Moving Average, Linear Regression, Random Forest, ARIMA), held-out metrics (MAE, RMSE, MAPE, R²), side-by-side **Model Comparison** (`/forecasting/compare/`), and all 6 MSc IT **Experiment Lab** research protocols.
- **Reports (`reports`):** Multi-section **InsightLab PDF Reports** generated via ReportLab and Matplotlib from real dataset profiles, cleaning logs, charts, anomalies, evaluations, and insights.

## General-Purpose vs. Sales-Specific Scope

- **General-purpose capabilities:** CSV upload/validation (min 2 columns), automatic schema detection across arbitrary column names, general profiling across all numeric/categorical columns, non-destructive cleaning across all columns, Data Explorer, custom Visualization Studio, cross-sectional & time-series Z-score anomaly detection, and forecasting on any mapped date column + numerical target column (verified with Website Traffic, Business Expenses, and non-date tabular datasets).
- **Remaining sales-specific / compatibility details:** Internal Django app/model names (`SalesDataset`, `date_column`, `product_column`, `quantity_column`, `revenue_column`) are preserved intentionally for database and migration stability. The Experiment Lab's 6 academic protocols remain framed around the MSc IT sales forecasting research study while accepting any mapped dataset/target.

## Automated Test Status

- **Last verified run (`manage.py test -v 2`):** **20 of 20 tests passed** in `13.423s` across `accounts` (6), `datasets` (4), `analytics` (3), `forecasting` (6), and `reports` (1).

## Known Bugs & Technical Limitations

- **Deep-file CSV parse errors:** `validate_csv_upload` inspects the first 64 KB (`nrows=5`) at upload time; malformed syntax deeper in very large CSVs is caught during schema inspection/cleaning via view error handling rather than pre-upload scanning.
- **Dataset-specific forecasting requirements:** Datasets without a date column cannot run time-series forecasting (gracefully blocked with an informative message), and very short series (`< 8` periods) fall back from ARIMA/ML lags to baseline behavior.
- **Statistical significance:** Experiment Lab reports empirical error metrics (`MAE`, `RMSE`, `MAPE`, `R²`) on chronological splits and does not perform formal hypothesis testing.

## Remaining / Optional Future Work

- Excel (`.xlsx`) upload support.
- Optional XGBoost forecasting model and SARIMAX exogenous features.
- Formal statistical significance tests (Diebold–Mariano / Wilcoxon) for multi-dataset academic benchmarks.
- Production PostgreSQL + HTTPS deployment setup (`DJANGO_DEBUG=False`).
