"""
Automated tests for PDF report generation, download, deletion, and ownership isolation.
"""

import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from datasets.data_processing import generate_sample_sales_csv_bytes, profile_dataset
from datasets.models import SalesDataset

from .models import GeneratedReport


class ReportGenerationTests(TestCase):
    """Verify PDF report generation uses real data and enforces ownership."""

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.user_a = User.objects.create_user(username='rep_user_a', password='StrongPassword!123')
        self.user_b = User.objects.create_user(username='rep_user_b', password='StrongPassword!123')

    def tearDown(self):
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_generate_download_and_delete_pdf_report(self):
        with override_settings(MEDIA_ROOT=self.temp_media):
            csv_file = SimpleUploadedFile(
                'sample.csv', generate_sample_sales_csv_bytes(), content_type='text/csv'
            )
            ds = SalesDataset.objects.create(
                user=self.user_a,
                name='Report Test Dataset',
                original_file=csv_file,
                date_column='sale_date',
                product_column='product_name',
                quantity_column='units_sold',
                revenue_column='sales_revenue',
                is_mapped=True,
            )
            ds.profile_summary = profile_dataset(ds.original_file.path, ds.get_column_mapping())
            ds.save()

            self.client.login(username='rep_user_a', password='StrongPassword!123')
            gen_res = self.client.post(reverse('reports:list'), {'dataset_id': ds.pk})
            self.assertRedirects(gen_res, reverse('reports:list'))

            report = GeneratedReport.objects.get(user=self.user_a)
            dl_res = self.client.get(reverse('reports:download', args=[report.pk]))
            self.assertEqual(dl_res.status_code, 200)
            self.assertEqual(dl_res['Content-Type'], 'application/pdf')
            pdf_bytes = b''.join(dl_res.streaming_content)
            self.assertTrue(pdf_bytes.startswith(b'%PDF-'))

            # User B cannot download or delete User A's report
            self.client.logout()
            self.client.login(username='rep_user_b', password='StrongPassword!123')
            self.assertEqual(self.client.get(reverse('reports:download', args=[report.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('reports:delete', args=[report.pk])).status_code, 404)
