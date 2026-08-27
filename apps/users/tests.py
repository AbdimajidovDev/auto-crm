from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient

from apps.inventory.models import StockAdjustment
from apps.products.models import Product, ProductBatch, ProductUnitMeasurement
from apps.store.models import Store, StoreUser
from apps.transfer.models import StockTransfer
from apps.users.models import Role, User
from apps.users.permissions import (
    ALL_PERMISSION_CODES,
    catalog_for_api,
    user_has_perm,
    user_permissions,
)


class GranularRBACAndSecurityTests(TestCase):
    """AutoCRM Granular RBAC, Report & Export Security, va Store Scoping testlari."""

    def setUp(self):
        self.client = APIClient()

        # Do'konlar
        self.store_a = Store.objects.create(name="112-do'kon", is_active=True)
        self.store_b = Store.objects.create(name="115-do'kon", is_active=True)

        # O'lchov birligi va mahsulot
        self.unit = ProductUnitMeasurement.objects.create(
            measurement="dona",
            quantity_type=ProductUnitMeasurement.QuantityType.WHOLE,
        )
        self.product = Product.objects.create(
            name="Zajim 01",
            sku="ZAJ-01",
            unit_measurement=self.unit,
            min_stock=Decimal("10"),
            status=Product.ProductStatus.ACTIVE,
        )
        self.batch_a = ProductBatch.objects.create(
            store=self.store_a,
            product=self.product,
            quantity=Decimal("100.00"),
            purchase_price=Decimal("50000.00"),
            selling_price=Decimal("75000.00"),
        )

        # Foydalanuvchilar
        self.superuser = User.objects.create(
            phone_number="+998901111111",
            full_name="Super Admin",
            is_superuser=True,
            is_staff=True,
        )
        self.roleless_user = User.objects.create(
            phone_number="+998902222222",
            full_name="Rolsiz User",
            is_superuser=False,
            role=None,
        )

        # Maxsus rollar
        self.sales_reporter_role = Role.objects.create(
            name="Faqat Sotuv Hisoboti Ko'ruvchi",
            permissions=["reports.view", "reports.sales.view", "dashboard.view"],
        )
        self.sales_reporter_user = User.objects.create(
            phone_number="+998903333333",
            full_name="Sotuv Reporter",
            is_superuser=False,
            role=self.sales_reporter_role,
        )
        StoreUser.objects.create(user=self.sales_reporter_user, store=self.store_a)

        self.sales_exporter_role = Role.objects.create(
            name="Sotuv Hisoboti Export Qiluvchi",
            permissions=["reports.view", "reports.sales.view", "reports.sales.export", "dashboard.view"],
        )
        self.sales_exporter_user = User.objects.create(
            phone_number="+998904444444",
            full_name="Sotuv Exporter",
            is_superuser=False,
            role=self.sales_exporter_role,
        )
        StoreUser.objects.create(user=self.sales_exporter_user, store=self.store_a)

    def test_permission_catalog_integrity(self):
        """Katalogdagi barcha kodlar unique va ALL_PERMISSION_CODES ga to'g'ri tushishini tekshirish."""
        catalog = catalog_for_api()
        self.assertTrue(len(catalog) >= 10)

        all_actions = [a["code"] for m in catalog for a in m["actions"]]
        self.assertEqual(len(all_actions), len(set(all_actions)), "Dublikat permission kodlari topildi!")

        for code in all_actions:
            self.assertIn(code, ALL_PERMISSION_CODES)

    def test_superuser_bypass(self):
        """Superuser barcha huquqlarga ega (user_permissions=None, user_has_perm=True)."""
        self.assertIsNone(user_permissions(self.superuser))
        self.assertTrue(user_has_perm(self.superuser, "reports.view"))
        self.assertTrue(user_has_perm(self.superuser, "reports.payments.view"))
        self.assertTrue(user_has_perm(self.superuser, "products.stock.adjust"))
        self.assertTrue(user_has_perm(self.superuser, "anything.arbitrary"))

    def test_roleless_user_fail_closed(self):
        """Rolsiz user hech qanday huquqqa ega emas."""
        perms = user_permissions(self.roleless_user)
        self.assertEqual(perms, frozenset())
        self.assertFalse(user_has_perm(self.roleless_user, "dashboard.view"))
        self.assertFalse(user_has_perm(self.roleless_user, "products.view"))
        self.assertFalse(user_has_perm(self.roleless_user, "reports.view"))

    def test_reports_module_view_required_hierarchy(self):
        """reports.view (modulga kirish) bo'lmasa, hatto child report huquqi bo'lsa ham 403 qaytaradi."""
        no_module_role = Role.objects.create(
            name="No Module Reports",
            permissions=["reports.sales.view"],
        )
        user = User.objects.create(
            phone_number="+998909999999",
            full_name="No Module User",
            role=no_module_role,
        )
        StoreUser.objects.create(user=user, store=self.store_a)

        self.client.force_authenticate(user=user)
        resp = self.client.get("/api/reports/builder/?report_type=sales")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("permission"), "reports.view")

        # reports.view qo'shilgach, sales hisoboti 200 OK qaytaradi
        no_module_role.permissions.append("reports.view")
        no_module_role.save()

        resp_ok = self.client.get("/api/reports/builder/?report_type=sales")
        self.assertEqual(resp_ok.status_code, 200)

    def test_granular_report_permissions(self):
        """Foydalanuvchida faqat reports.sales.view bo'lsa, sales ko'rinadi, payments 403 bo'ladi."""
        self.client.force_authenticate(user=self.sales_reporter_user)

        # 1. reports.sales.view -> 200 OK
        resp_sales = self.client.get("/api/reports/builder/?report_type=sales")
        self.assertEqual(resp_sales.status_code, 200)

        # 2. reports.payments.view -> 403 Forbidden
        resp_payments = self.client.get("/api/reports/builder/?report_type=payments")
        self.assertEqual(resp_payments.status_code, 403)
        self.assertIn("reports.payments.view", resp_payments.json().get("permission", ""))

    def test_report_view_true_export_false_isolation(self):
        """Ko'rish huquqi bor, lekin export huquqi bo'lmasa -> generate 200, export 403."""
        # sales_reporter_user da faqat reports.sales.view bor
        self.client.force_authenticate(user=self.sales_reporter_user)

        resp_view = self.client.get("/api/reports/builder/?report_type=sales")
        self.assertEqual(resp_view.status_code, 200)

        resp_export = self.client.get("/api/reports/builder/export/?report_type=sales&export_type=csv")
        self.assertEqual(resp_export.status_code, 403)
        self.assertIn("reports.sales.export", resp_export.json().get("permission", ""))

        # sales_exporter_user da esa reports.sales.export ham bor
        self.client.force_authenticate(user=self.sales_exporter_user)
        resp_export_ok = self.client.get("/api/reports/builder/export/?report_type=sales&export_type=csv")
        self.assertEqual(resp_export_ok.status_code, 200)

    def test_meta_filtering_by_user_permission(self):
        """_scoped_meta faqat ruxsat berilgan hisobotlarni qaytaradi."""
        self.client.force_authenticate(user=self.sales_reporter_user)

        resp = self.client.get("/api/reports/builder/meta/")
        self.assertEqual(resp.status_code, 200)
        report_keys = [r["key"] for r in resp.data["reports"]]

        self.assertIn("sales", report_keys)
        self.assertNotIn("payments", report_keys)
        self.assertNotIn("expenses", report_keys)

    def test_product_stock_adjust_permission_enforcement(self):
        """products.stock.adjust huquqi bo'lmasa qoldiqni qo'lda o'zgartirish 403 qaytaradi."""
        seller_role = Role.objects.create(
            name="Oddiy Sotuvchi",
            permissions=["products.view", "sales.create", "sales.view"],
        )
        seller = User.objects.create(
            phone_number="+998905555555",
            full_name="Sotuvchi Ali",
            role=seller_role,
        )
        StoreUser.objects.create(user=seller, store=self.store_a)

        self.client.force_authenticate(user=seller)
        url = f"/api/products/{self.product.id}/update-stocks/"
        payload = {
            "stores": [
                {
                    "store_id": self.store_a.id,
                    "quantity": "110.00",
                    "reason": "Qayta sanash",
                }
            ]
        }
        resp = self.client.post(url, payload, format="json")
        self.assertEqual(resp.status_code, 403)

        # products.stock.adjust berilganda muvaffaqiyatli o'tadi
        seller_role.permissions.append("products.stock.adjust")
        seller_role.save()

        resp_ok = self.client.post(url, payload, format="json")
        self.assertEqual(resp_ok.status_code, 200)

    def test_import_and_writeoff_cancel_permission(self):
        """import.cancel va writeoff.cancel alohida tekshiriladi."""
        from apps.inventory.services.stock_adjustment_service import StockAdjustmentService

        adj = StockAdjustmentService.create_adjustment(
            store_id=self.store_a.id,
            product_id=self.product.id,
            quantity=Decimal("5"),
            type=StockAdjustment.Type.IMPORT,
            user=self.superuser,
        )

        user_no_cancel = User.objects.create(
            phone_number="+998906666666",
            full_name="No Cancel User",
            role=Role.objects.create(name="No Cancel", permissions=["import.view", "import.create"]),
        )
        StoreUser.objects.create(user=user_no_cancel, store=self.store_a)

        self.client.force_authenticate(user=user_no_cancel)
        resp = self.client.post(f"/api/inventory/adjustments/{adj.id}/cancel/")
        self.assertEqual(resp.status_code, 403)

        # Cancel ruxsati berilganda muvaffaqiyatli bo'ladi
        user_no_cancel.role.permissions.append("import.cancel")
        user_no_cancel.role.save()

        resp_ok = self.client.post(f"/api/inventory/adjustments/{adj.id}/cancel/")
        self.assertEqual(resp_ok.status_code, 200)

    def test_transfer_approve_permission(self):
        """transfers.approve huquqi bo'lmasa transfer tasdiqlash 403 qaytaradi."""
        transfer = StockTransfer.objects.create(
            from_store=self.store_a,
            to_store=self.store_b,
            created_by=self.superuser,
        )

        user_viewer = User.objects.create(
            phone_number="+998907777777",
            full_name="Transfer Viewer",
            role=Role.objects.create(name="Transfer Viewer", permissions=["transfers.view"]),
        )
        StoreUser.objects.create(user=user_viewer, store=self.store_b)

        self.client.force_authenticate(user=user_viewer)
        resp = self.client.post(f"/api/transfer/{transfer.id}/approve/")
        self.assertEqual(resp.status_code, 403)

        user_viewer.role.permissions.append("transfers.approve")
        user_viewer.role.save()

        resp_ok = self.client.post(f"/api/transfer/{transfer.id}/approve/")
        self.assertEqual(resp_ok.status_code, 200)

    def test_store_scoping_in_reports(self):
        """Xodim o'ziga biriktirilmagan do'kon hisobotini olmoqchi bo'lsa server faqat o'z do'koniga moslaydi."""
        self.client.force_authenticate(user=self.sales_reporter_user)

        # 115-do'kon so'ralsa ham xodim faqat 112-do'konga biriktirilgani uchun server xavfsiz ishlaydi
        resp = self.client.get(f"/api/reports/builder/?report_type=sales&store_id={self.store_b.id}")
        self.assertEqual(resp.status_code, 200)
