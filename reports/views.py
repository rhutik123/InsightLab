"""
Views for generating, listing, downloading, and deleting InsightLab PDF reports.
"""

from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from analytics.analysis import compute_general_eda, detect_anomalies
from analytics.decision_support import generate_business_insights
from analytics.views import _load_active_general_df
from datasets.data_processing import load_standardized_dataframe
from datasets.models import SalesDataset, WorkspaceActivity
from forecasting.models import ForecastExperiment
from forecasting.preprocessing import prepare_time_series

from .models import GeneratedReport
from .report_generator import build_sales_report_pdf


@login_required
def report_list_view(request):
    """List saved PDF reports and generate a new InsightLab PDF report for any mapped dataset."""
    datasets = SalesDataset.objects.filter(user=request.user, is_mapped=True)
    reports = GeneratedReport.objects.filter(user=request.user).select_related('dataset')

    if request.method == 'POST':
        dataset_id = request.POST.get('dataset_id')
        dataset = get_object_or_404(SalesDataset, pk=dataset_id, user=request.user, is_mapped=True)

        try:
            df, schema, used_version = _load_active_general_df(dataset, 'cleaned')
        except FileNotFoundError:
            messages.error(request, 'Dataset CSV file could not be found on disk.')
            return redirect('reports:list')

        target_col = schema.get('primary_target_column') or ''
        date_col = schema.get('primary_date_column') or ''
        group_col = schema.get('primary_group_column') or ''
        secondary_col = schema.get('secondary_numeric_column') or ''

        eda_data = compute_general_eda(
            df,
            date_col=date_col,
            target_col=target_col,
            group_col=group_col,
            secondary_col=secondary_col,
        )
        anomaly_data = detect_anomalies(
            df,
            target_col=eda_data.get('target_col') or target_col,
            date_col=date_col,
            group_col=group_col,
        )
        experiments = list(
            ForecastExperiment.objects.filter(user=request.user, dataset=dataset).order_by('-created_at')
        )

        insights = []
        if experiments:
            insights = experiments[0].insights_json or []
        if not insights and date_col and target_col and not df.empty:
            file_path = dataset.cleaned_file.path if used_version == 'cleaned' else dataset.source_file_path
            std_df = load_standardized_dataframe(
                file_path,
                {'date': date_col, 'product': group_col, 'quantity': secondary_col, 'revenue': target_col},
            )
            series, _ = prepare_time_series(std_df, '__ALL__', 'revenue', 'D', 'zero_fill')
            insights = generate_business_insights(
                series,
                [],
                target_col=target_col,
                product_label='All Records',
                anomalies=anomaly_data.get('anomalies', []),
            )

        pdf_bytes = build_sales_report_pdf(
            dataset=dataset,
            eda_data=eda_data,
            anomaly_data=anomaly_data,
            experiments=experiments,
            insights=insights,
        )

        timestamp_slug = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'insightlab_report_{dataset.pk}_{timestamp_slug}.pdf'
        report = GeneratedReport(
            user=request.user,
            dataset=dataset,
            title=f'{dataset.name} — InsightLab Report',
        )
        report.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)

        WorkspaceActivity.objects.create(
            user=request.user,
            action_type='report',
            message=f'Generated InsightLab PDF report for "{dataset.name}".',
        )
        messages.success(request, 'InsightLab PDF report generated and saved.')
        return redirect('reports:list')

    context = {
        'active_nav': 'reports',
        'page_title': 'Reports',
        'datasets': datasets,
        'reports': reports,
    }
    return render(request, 'reports/report_list.html', context)


@login_required
def report_download_view(request, pk):
    """Stream an owned PDF report file as an attachment."""
    report = get_object_or_404(GeneratedReport, pk=pk, user=request.user)
    try:
        file_handle = report.pdf_file.open('rb')
    except FileNotFoundError as exc:
        raise Http404('Report PDF file could not be found on disk.') from exc
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=f'insightlab_report_{report.pk}.pdf',
        content_type='application/pdf',
    )


@login_required
@require_POST
def report_delete_view(request, pk):
    """Delete an owned PDF report."""
    report = get_object_or_404(GeneratedReport, pk=pk, user=request.user)
    if report.pdf_file:
        report.pdf_file.delete(save=False)
    report.delete()
    messages.success(request, 'Report deleted.')
    return redirect('reports:list')
