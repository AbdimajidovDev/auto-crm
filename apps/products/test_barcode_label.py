import io
import os
from decimal import Decimal
from PIL import Image
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
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


class BarcodeLabelDeviceImageUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create(
            phone_number="+998901234567",
            full_name="Label Manager",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.user)

        self.store = Store.objects.create(name="Upload Test Store", is_active=True)
        self.product = Product.objects.create(
            name="Rasm Mahsuloti",
            sku="IMG-SKU-001",
            barcode="2000000011448",
        )
        ProductBatch.objects.create(
            product=self.product,
            store=self.store,
            quantity=Decimal("10"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("120000"),
        )
        self.created_files = []

    def tearDown(self):
        for path in self.created_files:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass

    def _track_file(self, file_path: str):
        full = os.path.join(str(settings.MEDIA_ROOT), file_path.lstrip("/"))
        self.created_files.append(full)

    def _create_image(self, name: str, fmt: str = "PNG", size=(60, 60), color="red"):
        buf = io.BytesIO()
        img = Image.new("RGB", size, color=color)
        img.save(buf, format=fmt)
        buf.seek(0)
        return SimpleUploadedFile(name, buf.getvalue(), content_type=f"image/{fmt.lower()}")

    def test_1_authenticated_image_upload(self):
        image_file = self._create_image("test_auth.png", "PNG")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn("url", data)
        self.assertIn("file_path", data)
        self._track_file(data["file_path"])

    def test_1b_upload_image_via_barcode_templates_action(self):
        image_file = self._create_image("test_action.png", "PNG")
        response = self.client.post(
            "/api/products/barcode-templates/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn("url", data)
        self.assertIn("file_path", data)
        self._track_file(data["file_path"])

    def test_2_unauthenticated_upload_rejected(self):
        anon_client = APIClient()
        image_file = self._create_image("test_anon.png", "PNG")
        response = anon_client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_3_valid_jpg_upload(self):
        image_file = self._create_image("test_valid.jpg", "JPEG", color="green")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertTrue(data["url"].endswith(".jpg"))
        self._track_file(data["file_path"])

    def test_4_valid_png_upload(self):
        image_file = self._create_image("test_valid.png", "PNG", color="blue")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertTrue(data["url"].endswith(".png"))
        self._track_file(data["file_path"])

    def test_5_valid_webp_upload(self):
        image_file = self._create_image("test_valid.webp", "WEBP", color="yellow")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertTrue(data["url"].endswith(".webp"))
        self._track_file(data["file_path"])

    def test_6_invalid_non_image_file_rejected(self):
        fake_file = SimpleUploadedFile("fake.jpg", b"This is not a real image header", content_type="image/jpeg")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": fake_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.json())

    def test_7_oversized_file_rejected(self):
        # 5 MB + 1024 bytes
        large_content = b"X" * (5 * 1024 * 1024 + 1024)
        large_file = SimpleUploadedFile("oversized.png", large_content, content_type="image/png")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": large_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("5 MB", response.json()["detail"])

    def test_8_uploaded_image_url_returned_correctly(self):
        image_file = self._create_image("test_url.png", "PNG")
        response = self.client.post(
            "/api/products/barcode-labels/upload-image/",
            {"image": image_file},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertTrue(data["url"].startswith(settings.MEDIA_URL))
        full_path = os.path.join(str(settings.MEDIA_ROOT), data["file_path"])
        self._track_file(data["file_path"])
        self.assertTrue(os.path.isfile(full_path))

    def test_9_template_save_with_uploaded_source_passes_validator(self):
        image_file = self._create_image("test_tmpl.png", "PNG")
        res = self.client.post("/api/products/barcode-labels/upload-image/", {"image": image_file}, format="multipart")
        data = res.json()
        self._track_file(data["file_path"])

        layout = {
            "version": 1,
            "background_color": "#FFFFFF",
            "elements": [
                {
                    "id": "elem_uploaded_logo",
                    "type": "image",
                    "source_type": "uploaded",
                    "image_url": data["url"],
                    "x_mm": 2.0,
                    "y_mm": 2.0,
                    "width_mm": 10.0,
                    "height_mm": 10.0,
                    "image_fit": "contain",
                    "z_index": 1,
                    "visible": True,
                }
            ],
        }

        cleaned, warnings = LabelTemplateValidator.validate(layout, 30.0, 19.0)
        self.assertEqual(len(cleaned["elements"]), 1)
        el = cleaned["elements"][0]
        self.assertEqual(el["source_type"], "uploaded")
        self.assertEqual(el["image_url"], data["url"])
        self.assertIsNone(el["field"])

    def test_10_existing_company_logo_source_still_works(self):
        layout = {
            "version": 1,
            "background_color": "#FFFFFF",
            "elements": [
                {
                    "id": "elem_company_logo",
                    "type": "image",
                    "source_type": "field",
                    "field": "company.logo",
                    "x_mm": 1.0,
                    "y_mm": 1.0,
                    "width_mm": 8.0,
                    "height_mm": 8.0,
                    "image_fit": "contain",
                    "z_index": 1,
                    "visible": True,
                }
            ],
        }
        cleaned, _ = LabelTemplateValidator.validate(layout, 30.0, 19.0)
        el = cleaned["elements"][0]
        self.assertEqual(el["source_type"], "field")
        self.assertEqual(el["field"], "company.logo")

    def test_11_existing_product_image_source_still_works(self):
        # Also test with omitted source_type (backward compatibility)
        layout = {
            "version": 1,
            "background_color": "#FFFFFF",
            "elements": [
                {
                    "id": "elem_prod_photo",
                    "type": "image",
                    "field": "product.image",
                    "x_mm": 1.0,
                    "y_mm": 1.0,
                    "width_mm": 10.0,
                    "height_mm": 10.0,
                    "image_fit": "cover",
                    "z_index": 1,
                    "visible": True,
                }
            ],
        }
        cleaned, _ = LabelTemplateValidator.validate(layout, 30.0, 19.0)
        el = cleaned["elements"][0]
        self.assertEqual(el["source_type"], "field")
        self.assertEqual(el["field"], "product.image")

    def test_12_uploaded_image_renders_in_png_preview_and_pdf_print(self):
        image_file = self._create_image("test_render.png", "PNG", size=(40, 40), color="purple")
        res = self.client.post("/api/products/barcode-labels/upload-image/", {"image": image_file}, format="multipart")
        data = res.json()
        self._track_file(data["file_path"])

        layout = {
            "version": 1,
            "background_color": "#FFFFFF",
            "elements": [
                {
                    "id": "elem_img",
                    "type": "image",
                    "source_type": "uploaded",
                    "image_url": data["url"],
                    "x_mm": 2.0,
                    "y_mm": 2.0,
                    "width_mm": 10.0,
                    "height_mm": 10.0,
                    "image_fit": "contain",
                    "z_index": 1,
                    "visible": True,
                }
            ],
        }

        # 1. PngLabelRenderer
        context = LabelDataResolver.resolve_context(self.product, self.store)
        png_bytes = PngLabelRenderer.render_png(30.0, 19.0, layout, context)
        self.assertTrue(len(png_bytes) > 0)
        img = Image.open(io.BytesIO(png_bytes))
        self.assertEqual(img.format, "PNG")

        # 2. PdfLabelRenderer
        pdf_bytes = PdfLabelRenderer.render_pdf(30.0, 19.0, layout, [(context, 2)])
        self.assertTrue(len(pdf_bytes) > 0)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

        # 3. BarcodeLabelPreviewAPIView endpoint with layout_override
        preview_res = self.client.post(
            "/api/products/barcode-labels/preview/",
            {
                "product_id": self.product.id,
                "store_id": self.store.id,
                "layout_override": layout,
            },
            format="json",
        )
        self.assertEqual(preview_res.status_code, status.HTTP_200_OK)
        self.assertEqual(preview_res["Content-Type"], "image/png")


class ProductBarcodePrintRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create(
            phone_number="+998901112233",
            full_name="Print Admin",
            is_superuser=True,
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin)

        self.store1 = Store.objects.create(name="Asosiy Do'kon", is_active=True)
        self.store2 = Store.objects.create(name="Filial Do'kon", is_active=True)

        BarcodeTemplate.objects.filter(is_default=True).update(is_default=False)
        self.custom_template = BarcodeTemplate.objects.create(
            name="Maxsus Visual Shablon 40x25",
            width_mm=Decimal("40.00"),
            height_mm=Decimal("25.00"),
            barcode_format="EAN13",
            is_default=True,
            layout={
                "version": 1,
                "background_color": "#FFFFFF",
                "elements": [
                    {
                        "id": "elem_company",
                        "type": "text",
                        "field": "company.name",
                        "x_mm": 2.0,
                        "y_mm": 1.0,
                        "width_mm": 36.0,
                        "height_mm": 4.0,
                        "font_size": 8,
                        "font_weight": "bold",
                        "align": "center",
                        "visible": True,
                    },
                    {
                        "id": "elem_name",
                        "type": "text",
                        "field": "product.name",
                        "x_mm": 2.0,
                        "y_mm": 5.5,
                        "width_mm": 36.0,
                        "height_mm": 4.0,
                        "font_size": 7,
                        "align": "left",
                        "visible": True,
                    },
                    {
                        "id": "elem_sku",
                        "type": "text",
                        "field": "product.sku",
                        "x_mm": 2.0,
                        "y_mm": 10.0,
                        "width_mm": 18.0,
                        "height_mm": 3.5,
                        "font_size": 6,
                        "align": "left",
                        "visible": True,
                    },
                    {
                        "id": "elem_price",
                        "type": "text",
                        "field": "product.batch.selling_price",
                        "x_mm": 20.0,
                        "y_mm": 10.0,
                        "width_mm": 18.0,
                        "height_mm": 3.5,
                        "font_size": 7,
                        "font_weight": "bold",
                        "align": "right",
                        "visible": True,
                    },
                    {
                        "id": "elem_barcode",
                        "type": "barcode",
                        "field": "product.barcode",
                        "x_mm": 3.0,
                        "y_mm": 14.0,
                        "width_mm": 34.0,
                        "height_mm": 9.0,
                        "show_text": True,
                        "visible": True,
                    },
                ],
            },
        )

        self.product = Product.objects.create(
            name="Amortizator Cobalt",
            sku="AMORT-COB-01",
            barcode="2000000011448",
        )

        ProductBatch.objects.create(
            product=self.product,
            store=self.store1,
            quantity=Decimal("10"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("150000"),
            is_active=True,
        )

        ProductBatch.objects.create(
            product=self.product,
            store=self.store2,
            quantity=Decimal("5"),
            purchase_price=Decimal("100000"),
            selling_price=Decimal("175000"),
            is_active=True,
        )

    def test_01_product_print_resolves_saved_default_template(self):
        """When template_id is omitted, print endpoint resolves the active default template."""
        res = self.client.post(
            "/api/products/barcode-labels/print/",
            {
                "store_id": self.store1.id,
                "items": [{"product_id": self.product.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))

    def test_02_custom_template_fields_rendered(self):
        """Custom template fields (company, product name, sku, price) are resolved in context."""
        ctx = LabelDataResolver.resolve_context(self.product, self.store1)
        self.assertEqual(ctx["company.name"], "AVTOYON")
        self.assertEqual(ctx["product.name"], "Amortizator Cobalt")
        self.assertEqual(ctx["product.sku"], "AMORT-COB-01")
        self.assertEqual(ctx["product.batch.selling_price"], "150 000 so'm")

        # Renders without error
        pdf_bytes = PdfLabelRenderer.render_pdf(
            float(self.custom_template.width_mm),
            float(self.custom_template.height_mm),
            self.custom_template.layout,
            [(ctx, 1)],
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_03_barcode_value_remains_correct(self):
        """Barcode value 2000000011448 is properly preserved and encoded to 95 modules."""
        res = BarcodeGeneratorService.generate(self.product.barcode)
        self.assertEqual(res.barcode_value, "2000000011448")
        self.assertEqual(res.total_modules, 95)

    def test_04_store_specific_price_is_correct(self):
        """Price resolution respects the requested store ID (store1: 150 000, store2: 175 000)."""
        ctx_store1 = LabelDataResolver.resolve_context(self.product, self.store1)
        ctx_store2 = LabelDataResolver.resolve_context(self.product, self.store2)
        self.assertEqual(ctx_store1["product.batch.selling_price"], "150 000 so'm")
        self.assertEqual(ctx_store2["product.batch.selling_price"], "175 000 so'm")

    def test_05_template_dimensions_are_respected(self):
        """PDF pagesize corresponds to width_mm and height_mm of the template."""
        from reportlab.lib.units import mm
        ctx = LabelDataResolver.resolve_context(self.product, self.store1)
        pdf_bytes = PdfLabelRenderer.render_pdf(
            float(self.custom_template.width_mm),
            float(self.custom_template.height_mm),
            self.custom_template.layout,
            [(ctx, 1)],
        )
        expected_width = float(self.custom_template.width_mm) * mm
        self.assertTrue(len(pdf_bytes) > 0)
        self.assertIn(f"{expected_width:.2f}".encode("ascii")[:4], pdf_bytes)

    def test_06_print_flow_does_not_fall_back_to_legacy_simple_barcode_renderer(self):
        """Print endpoint generates vector multi-element PDF stream with reportlab canvas."""
        res = self.client.post(
            "/api/products/barcode-labels/print/",
            {
                "template_id": self.custom_template.id,
                "store_id": self.store1.id,
                "items": [{"product_id": self.product.id, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.assertIn(b"/Producer (ReportLab", res.content)

    def test_07_existing_default_template_still_works(self):
        """Product print works seamlessly with the active default template without requiring manual selection."""
        self.assertTrue(BarcodeTemplate.objects.filter(is_default=True).exists())
        res = self.client.post(
            "/api/products/barcode-labels/print/",
            {
                "items": [{"product_id": self.product.id, "quantity": 2}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))

    def test_08_multi_product_multi_quantity_print(self):
        """Multi-product, multi-quantity print payload generates combined vector PDF with exact page count."""
        product2 = Product.objects.create(
            name="Tormoz Kolodkasi",
            sku="BRAKE-02",
            barcode="2000000022554",
        )
        ProductBatch.objects.create(
            product=product2,
            store=self.store1,
            quantity=Decimal("20"),
            purchase_price=Decimal("60000"),
            selling_price=Decimal("85000"),
            is_active=True,
        )

        res = self.client.post(
            "/api/products/barcode-labels/print/",
            {
                "template_id": self.custom_template.id,
                "store_id": self.store1.id,
                "items": [
                    {"product_id": self.product.id, "quantity": 3},
                    {"product_id": product2.id, "quantity": 2},
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))
        page_count = res.content.count(b"/Type /Page\n") + res.content.count(b"/Type /Page ") + res.content.count(b"/Type/Page")
        self.assertEqual(page_count, 5)

    def test_09_stock_entry_items_payload_format(self):
        """StockEntry module print payload format is fully accepted and resolved with store prices."""
        res = self.client.post(
            "/api/products/barcode-labels/print/",
            {
                "store_id": self.store2.id,
                "items": [
                    {"product_id": self.product.id, "quantity": 1},
                ],
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))


