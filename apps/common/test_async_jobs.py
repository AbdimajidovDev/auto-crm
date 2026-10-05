import io
import time
from datetime import timedelta
from decimal import Decimal
import openpyxl
import xlsxwriter

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from apps.common.models import AsyncJob
from apps.common.services.async_job_service import AsyncJobService
from apps.contract.models import Supplier, StockEntry
from apps.contract.services.stock_entry_import_service import StockEntryImportService
from apps.contract.views.stock_entry_excel_view import StockEntryImportAPIView
from apps.contract.views.export_views import SupplierExportAPIView
from apps.products.models import Product, Category, Brand, ProductUnitMeasurement
from apps.products.services.product_import_service import ProductImportService
from apps.products.views.product_excel_view import ProductImportAPIView
from apps.store.models import Store
from apps.users.models import User


@override_settings(ASYNC_JOB_ALWAYS_SYNC=True)
class AsyncJobSystemTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client = APIClient()

        self.superuser = User.objects.create_superuser(
            phone_number="+998900000001",
            password="testpassword123",
            full_name="Super Admin",
        )
        self.regular_user = User.objects.create_user(
            phone_number="+998900000002",
            password="testpassword123",
            full_name="Regular User",
        )
        self.other_user = User.objects.create_user(
            phone_number="+998900000003",
            password="testpassword123",
            full_name="Other User",
        )

        self.store = Store.objects.create(name="Asosiy Do'kon", type=Store.StoreType.BASE, is_active=True)
        self.supplier = Supplier.objects.create(name="Test Ta'minotchi", phone_number="+998901234567")

        self.unit = ProductUnitMeasurement.objects.filter(measurement="dona").first()
        if not self.unit:
            self.unit = ProductUnitMeasurement.objects.create(measurement="dona")

        self.product1 = Product.objects.create(
            name="Shtrixli Tovar 1",
            barcode="4780000111223",
            sku="SKU-ASYNC-001",
            unit_measurement=self.unit,
        )
        self.product2 = Product.objects.create(
            name="Shtrixli Tovar 2",
            barcode="4780000111224",
            sku="SKU-ASYNC-002",
            unit_measurement=self.unit,
        )

    def _create_stock_excel(self, rows_count=2):
        prods = [self.product1, self.product2]
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf)
        ws = wb.add_worksheet()
        ws.write_row(0, 0, ["Shtrix kod", "Artikul", "Nomi", "Miqdori", "Kirim narxi", "Sotish narxi", "Optom narx"])
        for i in range(min(rows_count, len(prods))):
            p = prods[i]
            ws.write_row(i + 1, 0, [
                p.barcode,
                p.sku,
                p.name,
                5,
                50000,
                70000,
                60000,
            ])
        wb.close()
        buf.seek(0)
        return buf

    def _create_product_excel(self, rows_count=3):
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf)
        ws = wb.add_worksheet()
        ws.write_row(0, 0, ["Nomi *", "Kategoriya", "Brend", "O'lchov birligi", "Tavsif", "Status", "Min. qoldiq", "Shtrix kod", "Artikul"])
        for i in range(1, rows_count + 1):
            ws.write_row(i, 0, [
                f"Yangi Tovar {i}",
                "",
                "",
                "dona",
                f"Tavsif {i}",
                "active",
                0,
                f"478900000{i:03d}1",
                f"SKU-NEW-{i}",
            ])
        wb.close()
        buf.seek(0)
        return buf

    # 1. AsyncJob model and service lifecycle
    def test_01_job_creation_and_execution_lifecycle(self):
        def sample_task(job, a, b):
            return {"sum": a + b}

        job = AsyncJobService.create_and_submit(
            job_type=AsyncJob.JobType.EXCEL_EXPORT,
            user=self.superuser,
            payload={"test": 123},
            task_func=sample_task,
            task_args=(10, 20),
        )
        self.assertIsNotNone(job.id)
        # Give worker a brief moment to finish
        time.sleep(0.3)
        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.JobStatus.COMPLETED)
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.result, {"sum": 30})

    # 2. Job Status API View
    def test_02_job_status_api(self):
        job = AsyncJob.objects.create(
            job_type=AsyncJob.JobType.STOCK_ENTRY_IMPORT,
            status=AsyncJob.JobStatus.PROCESSING,
            progress=50,
            created_by=self.superuser,
            store=self.store,
        )
        self.client.force_authenticate(user=self.superuser)
        res = self.client.get(f"/api/jobs/{job.id}/status/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "processing")
        self.assertEqual(res.data["progress"], 50)

    # 3. Job Cancellation API View
    def test_03_job_cancellation_api(self):
        job = AsyncJob.objects.create(
            job_type=AsyncJob.JobType.PRODUCT_IMPORT,
            status=AsyncJob.JobStatus.PENDING,
            created_by=self.superuser,
        )
        self.client.force_authenticate(user=self.superuser)
        res = self.client.post(f"/api/jobs/{job.id}/cancel/")
        self.assertEqual(res.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, AsyncJob.JobStatus.CANCELLED)

    # 4. Job Permission and Isolation
    def test_04_job_store_isolation_and_security(self):
        job = AsyncJob.objects.create(
            job_type=AsyncJob.JobType.EXCEL_EXPORT,
            status=AsyncJob.JobStatus.COMPLETED,
            created_by=self.superuser,
            store=self.store,
        )
        # Other user has no store access
        self.client.force_authenticate(user=self.other_user)
        res = self.client.get(f"/api/jobs/{job.id}/status/")
        self.assertEqual(res.status_code, 403)

    # 5. Stock Entry Async Import API (202 Accepted)
    def test_05_stock_entry_import_async_flow(self):
        buf = self._create_stock_excel(rows_count=2)
        upload = SimpleUploadedFile("kirim.xlsx", buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        self.client.force_authenticate(user=self.superuser)
        res = self.client.post(
            "/api/contract/entry/import/?async=true",
            {
                "supplier": self.supplier.id,
                "store": self.store.id,
                "cash_amount": "0",
                "card_amount": "0",
                "create_products": False,
                "file": upload,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 202)
        self.assertIn("job_id", res.data)
        job_id = res.data["job_id"]

        # Wait for worker
        time.sleep(0.8)
        job = AsyncJob.objects.get(id=job_id)
        self.assertEqual(job.status, AsyncJob.JobStatus.COMPLETED)
        self.assertEqual(job.result["created"], 2)

    # 6. Stock Entry Synchronous Import (Backward Compatibility)
    def test_06_stock_entry_import_sync_flow_preserved(self):
        buf = self._create_stock_excel(rows_count=1)
        upload = SimpleUploadedFile("kirim_sync.xlsx", buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        self.client.force_authenticate(user=self.superuser)
        res = self.client.post(
            "/api/contract/entry/import/",
            {
                "supplier": self.supplier.id,
                "store": self.store.id,
                "cash_amount": "0",
                "card_amount": "0",
                "create_products": False,
                "file": upload,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn("entry_id", res.data)
        self.assertEqual(res.data["created"], 1)

    # 7. Stock Entry Sync Row Guard (Exceeding MAX_SYNC_ROWS)
    def test_07_stock_entry_sync_row_guard(self):
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf)
        ws = wb.add_worksheet()
        ws.write_row(0, 0, ["Shtrix kod", "Artikul", "Nomi", "Miqdori", "Kirim narxi", "Sotish narxi", "Optom narx"])
        # Mock 10 rows with limit = 5
        for i in range(1, 10):
            ws.write_row(i, 0, [self.product1.barcode, self.product1.sku, self.product1.name, 1, 1000, 2000, 1500])
        wb.close()
        buf.seek(0)

        with self.assertRaises(Exception):
            StockEntryImportService.import_from_excel(
                file=buf,
                supplier=self.supplier,
                store=self.store,
                cash_amount=0,
                card_amount=0,
                user=self.superuser,
                max_rows=5,
            )

    # 8. Product Async Import API (202 Accepted)
    def test_08_product_import_async_flow(self):
        buf = self._create_product_excel(rows_count=2)
        upload = SimpleUploadedFile("products.xlsx", buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        self.client.force_authenticate(user=self.superuser)
        res = self.client.post(
            "/api/products/products/import/?async=true",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(res.status_code, 202)
        self.assertIn("job_id", res.data)
        job_id = res.data["job_id"]

        time.sleep(0.8)
        job = AsyncJob.objects.get(id=job_id)
        self.assertEqual(job.status, AsyncJob.JobStatus.COMPLETED)
        self.assertGreaterEqual(job.result.get("created", 0), 1)

    # 9. Excel Export Async Flow (202 Accepted + Download URL)
    def test_09_excel_export_async_flow(self):
        self.client.force_authenticate(user=self.superuser)
        res = self.client.get("/api/contract/supplier/export/?async=true")
        self.assertEqual(res.status_code, 202)
        self.assertIn("job_id", res.data)
        job_id = res.data["job_id"]

        time.sleep(0.8)
        job = AsyncJob.objects.get(id=job_id)
        self.assertEqual(job.status, AsyncJob.JobStatus.COMPLETED)
        self.assertTrue(bool(job.output_file))

        # Test download
        dl_res = self.client.get(f"/api/jobs/{job.id}/download/")
        self.assertEqual(dl_res.status_code, 200)
        self.assertEqual(dl_res["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertGreater(len(dl_res.getvalue()), 1000)

    # 10. Old Job Retention & Cleanup
    def test_10_cleanup_old_jobs(self):
        old_job = AsyncJob.objects.create(
            job_type=AsyncJob.JobType.EXCEL_EXPORT,
            status=AsyncJob.JobStatus.COMPLETED,
            created_by=self.superuser,
        )
        # Backdate created_at
        AsyncJob.objects.filter(id=old_job.id).update(created_at=timezone.now() - timedelta(hours=48))

        fresh_job = AsyncJob.objects.create(
            job_type=AsyncJob.JobType.EXCEL_EXPORT,
            status=AsyncJob.JobStatus.COMPLETED,
            created_by=self.superuser,
        )

        cleaned_count = AsyncJobService.cleanup_old_jobs(hours=24)
        self.assertEqual(cleaned_count, 1)
        self.assertFalse(AsyncJob.objects.filter(id=old_job.id).exists())
        self.assertTrue(AsyncJob.objects.filter(id=fresh_job.id).exists())
