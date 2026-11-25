"""
InsightLab downloadable PDF report generator built with ReportLab and Matplotlib.
Populates every table, chart, and insight strictly from actual dataset and experiment records,
adapting dynamically to any structured dataset schema.
"""

import io
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _render_trend_chart(eda_data):
    """Render primary trend or distribution chart as an in-memory PNG buffer."""
    target_label = eda_data.get('target_col', 'Value')
    if eda_data.get('has_date') and eda_data.get('daily_trend', {}).get('available'):
        labels = eda_data['daily_trend'].get('labels', [])
        values = eda_data['daily_trend'].get('values', [])
        if len(labels) < 2:
            return None
        fig, ax = plt.subplots(figsize=(6.4, 2.5), dpi=140)
        ax.plot(range(len(values)), values, color='#4f46e5', linewidth=1.8)
        ax.set_title(f'Historical "{target_label}" Trend', fontsize=10, fontweight='bold')
        ax.set_ylabel(target_label, fontsize=8)
        step = max(1, len(labels) // 6)
        ticks = list(range(0, len(labels), step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[i] for i in ticks], rotation=20, fontsize=7)
        ax.tick_params(axis='y', labelsize=7)
        ax.grid(alpha=0.25, linestyle='--')
        plt.tight_layout()
    elif eda_data.get('distribution', {}).get('has_chart'):
        dist = eda_data['distribution']
        fig, ax = plt.subplots(figsize=(6.4, 2.5), dpi=140)
        ax.bar(dist['labels'], dist['counts'], color='#4f46e5')
        ax.set_title(f'Distribution of "{target_label}"', fontsize=10, fontweight='bold')
        ax.set_ylabel('Records', fontsize=8)
        ax.tick_params(axis='x', rotation=20, labelsize=7)
        ax.tick_params(axis='y', labelsize=7)
        ax.grid(alpha=0.25, linestyle='--', axis='y')
        plt.tight_layout()
    else:
        return None

    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf


def _render_forecast_chart(exp_obj):
    """Render Actual vs. Predicted + Future Forecast PNG buffer."""
    res = exp_obj.results_json or {}
    train_vals = res.get('train_values', [])
    test_actuals = res.get('test_actuals', [])
    test_preds = res.get('test_predictions', [])
    future_preds = res.get('future_predictions', [])
    if not test_actuals and not future_preds:
        return None

    fig, ax = plt.subplots(figsize=(6.4, 2.5), dpi=140)
    tail_len = min(30, len(train_vals))
    tail_train = train_vals[-tail_len:] if tail_len else []
    x_train = list(range(len(tail_train)))
    x_test = list(range(len(tail_train), len(tail_train) + len(test_actuals)))
    x_future = list(
        range(len(tail_train) + len(test_actuals), len(tail_train) + len(test_actuals) + len(future_preds))
    )

    if tail_train:
        ax.plot(x_train, tail_train, color='#64748b', label='Recent Train', linewidth=1.5)
    if test_actuals:
        ax.plot(x_test, test_actuals, color='#0f172a', label='Test Actual', linewidth=1.8)
        ax.plot(x_test, test_preds, color='#4f46e5', linestyle='--', label='Test Predicted', linewidth=1.8)
    if future_preds:
        ax.plot(x_future, future_preds, color='#10b981', linestyle='--', label='Future Forecast', linewidth=1.8)

    ax.set_title(
        f'Forecast ({exp_obj.target_metric}): {exp_obj.get_model_name_display()}',
        fontsize=10,
        fontweight='bold',
    )
    ax.legend(fontsize=7, loc='upper left')
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.25, linestyle='--')
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf


def _styled_table(rows, col_widths=None):
    """Build a clean ReportLab table with consistent styling."""
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('TOPPADDING', (0, 0), (-1, 0), 6),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]
        )
    )
    return table


def build_sales_report_pdf(
    dataset,
    eda_data,
    anomaly_data,
    experiments,
    insights,
):
    """
    Generate a complete InsightLab PDF report in memory and return its bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ReportTitle', parent=styles['Title'], fontSize=18, textColor=colors.HexColor('#0f172a'), spaceAfter=4
    )
    sub_style = ParagraphStyle(
        'ReportSub', parent=styles['Normal'], fontSize=9.5, textColor=colors.HexColor('#4f46e5'), spaceAfter=6
    )
    h2_style = ParagraphStyle(
        'SectionH2', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#1e293b'), spaceBefore=12, spaceAfter=6
    )
    body_style = ParagraphStyle(
        'ReportBody', parent=styles['BodyText'], fontSize=9, leading=13, textColor=colors.HexColor('#334155')
    )

    story = []
    profile = dataset.active_profile

    # 1. Header
    story.append(Paragraph('InsightLab — Analytical &amp; Predictive Intelligence Report', title_style))
    story.append(Paragraph('Intelligent Data Analytics and Predictive Intelligence Platform &bull; Transform Data into Meaningful Insights', sub_style))
    story.append(
        Paragraph(
            f'<b>Dataset:</b> {xml_escape(str(dataset.name))} &nbsp;|&nbsp; '
            f'<b>Category:</b> {xml_escape(str(dataset.get_dataset_type_display()))} &nbsp;|&nbsp; '
            f'<b>Generated:</b> {datetime.now().strftime("%Y-%m-%d %H:%M")} &nbsp;|&nbsp; '
            f'<b>Version:</b> {"Cleaned" if dataset.has_cleaned_version else "Original"}',
            body_style,
        )
    )
    story.append(Spacer(1, 10))

    # 2. Dataset Overview
    story.append(Paragraph('1. Dataset Overview', h2_style))
    overview_rows = [
        ['Metric', 'Value', 'Metric', 'Value'],
        [
            'Total Rows',
            str(profile.get('row_count', 0)),
            'Total Columns',
            str(profile.get('col_count', 0)),
        ],
        [
            'Date Range',
            f'{profile.get("date_min") or "N/A"} to {profile.get("date_max") or "N/A"}',
            'Numerical / Categorical Cols',
            f'{profile.get("numeric_col_count", 0)} / {profile.get("categorical_col_count", 0)}',
        ],
        [
            'Primary Target Column',
            str(profile.get('primary_target_column') or dataset.primary_target_column or '—')[:28],
            'Missing Values / Duplicates',
            f'{profile.get("total_missing", 0)} / {profile.get("duplicate_rows", 0)}',
        ],
    ]
    story.append(_styled_table(overview_rows, col_widths=[1.45 * inch, 1.95 * inch, 1.85 * inch, 1.75 * inch]))

    # Numerical Columns Table
    num_stats = profile.get('numeric_columns_stats') or {}
    if num_stats:
        story.append(Spacer(1, 8))
        num_rows = [['Numerical Column', 'Count', 'Mean', 'Median', 'Min / Max', 'Sum']]
        for col_name, st in list(num_stats.items())[:8]:
            num_rows.append([
                str(col_name)[:24],
                str(st.get('count', 0)),
                f'{st["mean"]:.2f}' if st.get('mean') is not None else '—',
                f'{st["median"]:.2f}' if st.get('median') is not None else '—',
                f'{st["min"]} / {st["max"]}' if st.get('min') is not None else '—',
                f'{st.get("sum", 0):.2f}',
            ])
        story.append(_styled_table(num_rows, col_widths=[1.6 * inch, 0.7 * inch, 0.9 * inch, 0.9 * inch, 1.5 * inch, 1.4 * inch]))

    # 3. Data Cleaning Summary
    story.append(Paragraph('2. Data Cleaning Summary', h2_style))
    if dataset.cleaning_log:
        for op in dataset.cleaning_log:
            story.append(Paragraph(f'• {xml_escape(str(op))}', body_style))
    else:
        story.append(
            Paragraph('No cleaning pipeline has been applied yet; analysis reflects the original uploaded CSV.', body_style)
        )

    # 4. Exploratory Data Analysis
    story.append(Paragraph('3. Exploratory Data Analysis &amp; Segment Breakdown', h2_style))
    if eda_data and eda_data.get('has_data'):
        summary = eda_data['summary']
        t_col = eda_data.get('target_col', 'target')
        date_span_txt = (
            f'from {summary["date_start"]} to {summary["date_end"]} '
            if summary.get('date_start')
            else 'across all uploaded records '
        )
        story.append(
            Paragraph(
                f'Analyzed <b>{xml_escape(str(t_col))}</b> {date_span_txt}'
                f'({summary["active_days"]} period(s)/record(s)). '
                f'Mean: {summary["avg_target"]:.2f}, Median: {summary["median_target"]:.2f}, '
                f'Std Dev: {summary.get("std_target", 0):.2f}, Total: {summary["total_target"]:.2f}.',
                body_style,
            )
        )
        story.append(Spacer(1, 6))

        chart_buf = _render_trend_chart(eda_data)
        if chart_buf:
            story.append(Image(chart_buf, width=6.6 * inch, height=2.5 * inch))
            story.append(Spacer(1, 6))

        best_groups = eda_data.get('best_groups', [])
        if best_groups and eda_data.get('group_col'):
            g_col = eda_data['group_col']
            prod_rows = [[str(g_col)[:28], f'Total {t_col}'[:18], f'Mean {t_col}'[:18], 'Records']]
            for p in best_groups:
                prod_rows.append([
                    str(p['group'])[:30],
                    f'{p["total_target"]:.2f}',
                    f'{p["avg_target"]:.2f}',
                    str(p['record_count']),
                ])
            story.append(_styled_table(prod_rows, col_widths=[2.4 * inch, 1.6 * inch, 1.6 * inch, 1.4 * inch]))
    else:
        story.append(Paragraph('Insufficient valid numerical records for exploratory trend visualization.', body_style))

    # 5. Anomaly Detection Summary
    story.append(Paragraph('4. Statistical Anomaly Detection Summary', h2_style))
    anomalies = (anomaly_data or {}).get('anomalies', [])
    if anomalies:
        story.append(
            Paragraph(
                f'Detected {len(anomalies)} statistical deviation(s) using Z-score analysis. '
                'These are statistical observations rather than confirmed root causes.',
                body_style,
            )
        )
        story.append(Spacer(1, 4))
        anom_rows = [['Point / Date', 'Actual', 'Baseline', 'Diff', 'Z-Score', 'Observation']]
        for a in anomalies[:8]:
            anom_rows.append([
                a['date'],
                f'{a["actual"]:.2f}',
                f'{a["baseline"]:.2f}',
                f'{a["difference"]:+.2f}',
                f'{a["z_score"]:+.2f}',
                a['direction'],
            ])
        story.append(_styled_table(anom_rows, col_widths=[1.0 * inch, 0.9 * inch, 0.9 * inch, 0.9 * inch, 0.8 * inch, 2.5 * inch]))
    else:
        story.append(Paragraph('No statistically significant anomalies detected under current thresholds.', body_style))

    # 6. Forecasting & Model Comparison
    story.append(Paragraph('5. Forecasting Results &amp; Model Comparison', h2_style))
    if experiments:
        exp_rows = [['Model', 'Target / Scope', 'Train / Test', 'MAE', 'RMSE', 'MAPE (%)', 'R2']]
        for exp in experiments[:12]:
            exp_rows.append([
                exp.get_model_name_display(),
                f'{exp.target_metric} ({exp.product_display})'[:24],
                f'{exp.train_rows} / {exp.test_rows}',
                f'{exp.mae:.2f}' if exp.mae is not None else '—',
                f'{exp.rmse:.2f}' if exp.rmse is not None else '—',
                f'{exp.mape:.2f}' if exp.mape is not None else '—',
                f'{exp.r2:.3f}' if exp.r2 is not None else '—',
            ])
        story.append(
            _styled_table(
                exp_rows,
                col_widths=[1.7 * inch, 1.4 * inch, 0.85 * inch, 0.7 * inch, 0.8 * inch, 0.8 * inch, 0.75 * inch],
            )
        )
        story.append(Spacer(1, 8))

        best_exp = min(
            [e for e in experiments if e.rmse is not None],
            key=lambda e: e.rmse,
            default=experiments[0],
        )
        f_chart = _render_forecast_chart(best_exp)
        if f_chart:
            story.append(Image(f_chart, width=6.6 * inch, height=2.5 * inch))
    else:
        story.append(
            Paragraph('No forecasting experiments have been saved for this dataset yet.', body_style)
        )

    # 7. Decision Support Insights
    story.append(Paragraph('6. Analytical &amp; Decision Support Insights', h2_style))
    if insights:
        for ins in insights:
            story.append(
                Paragraph(
                    f'<b>{xml_escape(str(ins["title"]))}:</b> {xml_escape(str(ins["detail"]))}',
                    body_style,
                )
            )
            story.append(Spacer(1, 3))
    else:
        story.append(
            Paragraph('Run a forecast on a time-series dataset to generate quantitative outlook insights.', body_style)
        )

    # 8. Methodological Limitations
    story.append(Paragraph('7. Limitations &amp; Academic Integrity Notes', h2_style))
    limitations = [
        'Forecast accuracy is evaluated strictly on chronological held-out test splits; no algorithm is claimed to be universally optimal across all datasets.',
        'When operational capacity or inventory stock tables are not included in the dataset, outlook notes are labeled as planning suggestions.',
        'Detected anomalies are statistical Z-score observations and require human verification against domain records.',
    ]
    for lim in limitations:
        story.append(Paragraph(f'• {lim}', body_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
