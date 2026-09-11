import io
from decimal import Decimal
from PIL import Image
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.products.models import Product, ProductBatch, BarcodeTemplate, Brand, ProductUnitMeasurement
from apps.store.models import Store, StoreUser
from apps.users.models import User, Role
from apps.products.services.label_engine import (
    BarcodeGeneratorService,
    CompanyBrandingProvider,
    LabelTemplateValidator,
    LabelDataResolver,
    PdfLabelRenderer,
    PngLabelRenderer,
    LabelValidationError,
    LabelResolutionError,
)


class BarcodeGeneratorServiceTests(TestCase):
    def test_checksum_calculation(self):
        # 200000001144 -> checksum should be 8
        chk = BarcodeGeneratorService.calculate_checksum("200000001144")
        self.assertEqual(chk, "8")

    def test_normalize_ean13(self):
        normalized = BarcodeGeneratorService.normalize_ean13("200000001144")
        self.assertEqual(normalized, "2000000011448")

        # Wrong checksum normalized to correct
        fixed = BarcodeGeneratorService.normalize_ean13("2000000011440")
        self.assertEqual(fixed, "2000000011448")

    def test_generate_95_modules(self):
        res = BarcodeGeneratorService.generate("2000000011448")
        self.assertEqual(res.total_modules, 95)
        self.assertEqual(len(res.modules), 95)

        # Left guard: 101 (first 3)
        self.assertTrue(res.modules[0].is_black)
        self.assertFalse(res.modules[1].is_black)
        self.assertTrue(res.modules[2].is_black)
        self.assertTrue(res.modules[0].is_guard)

        # Center guard: 01010 (index 45..49)
        center = res.modules[45:50]
        self.assertEqual([m.is_black for m in center], [False, True, False, True, False])
        self.assertTrue(all(m.is_guard for m in center))

        # Right guard: 101 (last 3: index 92..94)
        right = res.modules[92:95]
        self.assertEqual([m.is_black for m in right], [True, False, True])
        self.assertTrue(all(m.is_guard for m in right))


class CompanyBrandingProviderTests(TestCase):
    def test_fallback_branding(self):
        name = CompanyBrandingProvider.get_name()
        self.assertEqual(name, "AVTOYON")


class LabelTemplateValidatorTests(TestCase):
    def setUp(self):
        self.valid_layout = {
            "version": 1,
            "background_color": "#FFFFFF",
            "elements": [
                {
                    "id": "el_1",
                    "type": "text",
                    "field": "product.name",
                    "x_mm": 1.0,
                    "y_mm": 1.0,
                    "width_mm": 20.0,
                    "height_mm": 5.0,
                    "z_index": 1,
                    "visible": True,
                },
                {
                    "id": "el_2",
                    "type": "barcode",
                    "field": "product.barcode",
                    "x_mm": 2.0,
                    "y_mm": 7.0,
                    "width_mm": 25.0,
                    "height_mm": 10.0,
                    "z_index": 2,
                    "visible": True,
                }
            ],
        }

    def test_single_source_of_truth_rejection(self):
        bad_layout = dict(self.valid_layout)
        bad_layout["width_mm"] = 30.0
        with self.assertRaises(LabelValidationError) as ctx:
            LabelTemplateValidator.validate(bad_layout, 30.0, 19.0)
        self.assertIn("Single source of truth", str(ctx.exception.message))

    def test_boundary_overflow_rejection(self):
        bad_layout = {
            "version": 1,
            "elements": [
                {
                    "id": "el_overflow",
                    "type": "text",
                    "x_mm": 20.0,
                    "y_mm": 1.0,
                    "width_mm": 15.0,  # 20 + 15 = 35 > 30 mm
                    "height_mm": 5.0,
                    "visible": True,
                }
            ]
        }
        with self.assertRaises(LabelValidationError) as ctx:
            LabelTemplateValidator.validate(bad_layout, 30.0, 19.0)
        self.assertIn("eni yorliq chegarasidan oshib ketdi", str(ctx.exception.message))

    def test_duplicate_id_rejection(self):
        bad_layout = {
            "version": 1,
            "elements": [
                {"id": "same_id", "type": "text", "x_mm": 0, "y_mm": 0, "width_mm": 5, "height_mm": 5, "visible": True},
                {"id": "same_id", "type": "text", "x_mm": 6, "y_mm": 0, "width_mm": 5, "height_mm": 5, "visible": True},
            ]
        }
        with self.assertRaises(LabelValidationError) as ctx:
            LabelTemplateValidator.validate(bad_layout, 30.0, 19.0)
        self.assertIn("takrorlanmas", str(ctx.exception.message))

    def test_image_security_rejects_arbitrary_url(self):
        bad_layout = {
            "version": 1,
            "elements": [
                {
                    "id": "el_img",
                    "type": "image",
                    "field": "company.logo",
                    "static_value": "http://evil.com/hack.png",  # Not allowed
                    "x_mm": 0, "y_mm": 0, "width_mm": 10, "height_mm": 5,
                    "visible": True,
                }
            ]
        }
        with self.assertRaises(LabelValidationError) as ctx:
            LabelTemplateValidator.validate(bad_layout, 30.0, 19.0)
        self.assertIn("static_value orqali ixtiyoriy URL", str(ctx.exception.message))

    def test_image_security_rejects_non_whitelisted_field(self):
        bad_layout = {
            "version": 1,
            "elements": [
                {
                    "id": "el_img",
                    "type": "image",
                    "field": "product.name",  # Invalid field for image
                    "x_mm": 0, "y_mm": 0, "width_mm": 10, "height_mm": 5,
                    "visible": True,
                }
            ]
        }
        with self.assertRaises(LabelValidationError) as ctx:
            LabelTemplateValidator.validate(bad_layout, 30.0, 19.0)
        self.assertIn("Rasm elementi uchun 'field' faqat quyidagilardan biri", str(ctx.exception.message))

    def test_advisory_warning_for_small_barcode(self):
        small_barcode_layout = {
            "version": 1,
            "elements": [
                {
                    "id": "el_small_bar",
                    "type": "barcode",
                    "field": "product.barcode",
                    "x_mm": 2.0, "y_mm": 2.0,
                    "width_mm": 15.0,  # < 20 mm
                    "height_mm": 5.0,   # < 6 mm
                    "visible": True,
                }
            ]
        }
        cleaned, warnings = LabelTemplateValidator.validate(small_barcode_layout, 30.0, 19.0)
        self.assertTrue(len(warnings) > 0)
        self.assertTrue(any("20x6 mm dan kichik" in w for w in warnings))


class LabelDataResolverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            phone_number="+998901112233",
            full_name="Staff User",
            is_staff=True,
            is_superuser=False,
        )
        self.base_store = Store.objects.create(name="Baza Ombor", type=Store.StoreType.BASE, is_active=True)
        self.branch_store = Store.objects.create(name="Filial Do'kon", type=Store.StoreType.STORE, is_active=True)
        StoreUser.objects.create(user=self.user, store=self.branch_store, is_active=True)

        self.brand = Brand.objects.create(name="Bosch")
        self.unit = ProductUnitMeasurement.objects.create(measurement="Dona")
        self.product = Product.objects.create(
            name="Amortizator",
            sku="AMORT-001",
            barcode="2000000011448",
            brand=self.brand,
            unit_measurement=self.unit,
        )

        self.batch = ProductBatch.objects.create(
            product=self.product,
            store=self.branch_store,
            quantity=Decimal("10"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("150000"),
            is_active=True,
        )

    def test_store_resolution_explicit(self):
        resolved = LabelDataResolver.resolve_store(self.user, self.branch_store.id)
        self.assertEqual(resolved.id, self.branch_store.id)

    def test_store_resolution_unauthorized_explicit_store(self):
        other_store = Store.objects.create(name="Begona Do'kon", is_active=True)
        with self.assertRaises(LabelResolutionError):
            LabelDataResolver.resolve_store(self.user, other_store.id)

    def test_store_resolution_implicit(self):
        resolved = LabelDataResolver.resolve_store(self.user, None)
        self.assertEqual(resolved.id, self.branch_store.id)

    def test_price_resolution(self):
        price_str = LabelDataResolver.resolve_price(self.product, self.branch_store)
        self.assertEqual(price_str, "150 000 so'm")

    def test_price_resolution_missing_batch_returns_empty_string(self):
        price_str = LabelDataResolver.resolve_price(self.product, self.base_store)
        self.assertEqual(price_str, "")

    def test_context_resolution(self):
        ctx = LabelDataResolver.resolve_context(self.product, self.branch_store)
        self.assertEqual(ctx["product.name"], "Amortizator")
        self.assertEqual(ctx["product.sku"], "AMORT-001")
        self.assertEqual(ctx["product.barcode"], "2000000011448")
        self.assertEqual(ctx["product.brand.name"], "Bosch")
        self.assertEqual(ctx["product.unit"], "Dona")
        self.assertEqual(ctx["product.batch.selling_price"], "150 000 so'm")
        self.assertEqual(ctx["company.name"], "AVTOYON")


class BarcodeTemplateAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(
            phone_number="+998909998877",
            full_name="Admin",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin)

        # Get or create default template
        self.default_template = BarcodeTemplate.objects.filter(is_default=True).first()
        if not self.default_template:
            self.default_template = BarcodeTemplate.objects.create(
                name="Avtoyon Standart 30x19",
                width_mm=Decimal("30.00"),
                height_mm=Decimal("19.00"),
                barcode_format="EAN13",
                is_default=True,
                layout={"version": 1, "elements": []},
            )

        self.store = Store.objects.create(name="Test Do'kon", is_active=True)
        self.product = Product.objects.create(
            name="Sinov Mahsuloti",
            sku="TEST-SKU",
            barcode="2000000011448",
        )
        ProductBatch.objects.create(
            product=self.product,
            store=self.store,
            quantity=Decimal("5"),
            purchase_price=Decimal("50000"),
            selling_price=Decimal("75000"),
            is_active=True,
        )

    def test_db_unique_constraint_on_default(self):
        # Attempting to create a second default directly in DB must fail
        with self.assertRaises(IntegrityError):
            BarcodeTemplate.objects.create(
                name="Duplicate Default",
                width_mm=Decimal("40.00"),
                height_mm=Decimal("25.00"),
                is_default=True,
                layout={"version": 1, "elements": []},
            )

    def test_list_templates(self):
        response = self.client.get("/api/products/barcode-templates/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify default template is present
        data = response.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        self.assertTrue(any(t["is_default"] for t in results))

    def test_create_template(self):
        payload = {
            "name": "Katta Yorliq 58x40",
            "description": "58x40 mm shabloni",
            "width_mm": 58.0,
            "height_mm": 40.0,
            "barcode_format": "EAN13",
            "is_default": False,
            "layout": {
                "version": 1,
                "background_color": "#FFFFFF",
                "elements": [
                    {
                        "id": "el_title",
                        "type": "text",
                        "field": "product.name",
                        "x_mm": 2.0,
                        "y_mm": 2.0,
                        "width_mm": 50.0,
                        "height_mm": 8.0,
                        "z_index": 1,
                        "visible": True,
                    }
                ],
            },
        }
        response = self.client.post("/api/products/barcode-templates/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["name"], "Katta Yorliq 58x40")

    def test_delete_non_default_template_hard_deletes(self):
        temp = BarcodeTemplate.objects.create(
            name="Vaqtinchalik Shablon",
            width_mm=Decimal("40.00"),
            height_mm=Decimal("20.00"),
            is_default=False,
            layout={"version": 1, "elements": []},
        )
        temp_id = temp.id
        response = self.client.delete(f"/api/products/barcode-templates/{temp_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BarcodeTemplate.objects.filter(id=temp_id).exists())

    def test_delete_default_template_promotes_next_template(self):
        second = BarcodeTemplate.objects.create(
            name="Ikkinchi Shablon",
            width_mm=Decimal("40.00"),
            height_mm=Decimal("20.00"),
            is_default=False,
            layout={"version": 1, "elements": []},
        )
        default_id = self.default_template.id
        response = self.client.delete(f"/api/products/barcode-templates/{default_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BarcodeTemplate.objects.filter(id=default_id).exists())
        second.refresh_from_db()
        self.assertTrue(second.is_default)

    def test_delete_sole_default_template_succeeds_without_default(self):
        BarcodeTemplate.objects.exclude(id=self.default_template.id).delete()
        default_id = self.default_template.id
        response = self.client.delete(f"/api/products/barcode-templates/{default_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(BarcodeTemplate.objects.count(), 0)

    def test_duplicate_template_action(self):
        response = self.client.post(f"/api/products/barcode-templates/{self.default_template.id}/duplicate/")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn("(Nusxa)", data["name"])
        self.assertFalse(data["is_default"])

    def test_set_default_action(self):
        new_template = BarcodeTemplate.objects.create(
            name="Yangi Standart Shablon",
            width_mm=Decimal("40.00"),
            height_mm=Decimal("25.00"),
            is_default=False,
            layout={"version": 1, "elements": []},
        )
        response = self.client.post(f"/api/products/barcode-templates/{new_template.id}/set-default/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new_template.refresh_from_db()
        self.default_template.refresh_from_db()
        self.assertTrue(new_template.is_default)
        self.assertFalse(self.default_template.is_default)

    def test_preview_api_returns_png(self):
        payload = {
            "product_id": self.product.id,
            "template_id": self.default_template.id,
            "store_id": self.store.id,
        }
        response = self.client.post("/api/products/barcode-labels/preview/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        # Verify valid PNG header
        img = Image.open(io.BytesIO(response.content))
        self.assertEqual(img.format, "PNG")

    def test_preview_api_with_layout_override(self):
        payload = {
            "product_id": self.product.id,
            "template_id": self.default_template.id,
            "layout_override": {
                "version": 1,
                "background_color": "#EEEEEE",
                "elements": [
                    {
                        "id": "el_override_text",
                        "type": "text",
                        "field": "product.name",
                        "x_mm": 1.0, "y_mm": 1.0, "width_mm": 25.0, "height_mm": 5.0,
                        "visible": True,
                    }
                ],
            }
        }
        response = self.client.post("/api/products/barcode-labels/preview/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_print_api_returns_pdf(self):
        payload = {
            "template_id": self.default_template.id,
            "store_id": self.store.id,
            "items": [
                {"product_id": self.product.id, "quantity": 3},
            ]
        }
        response = self.client.post("/api/products/barcode-labels/print/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
