from decimal import Decimal
from django.db import migrations


def seed_default_template(apps, schema_editor):
    BarcodeTemplate = apps.get_model("products", "BarcodeTemplate")
    if not BarcodeTemplate.objects.filter(is_default=True, is_active=True).exists():
        BarcodeTemplate.objects.create(
            name="Avtoyon Standart 30x19",
            description="Standart 30x19 mm termoyorliq shabloni (Avtoyon brendi bilan)",
            width_mm=Decimal("30.00"),
            height_mm=Decimal("19.00"),
            barcode_format="EAN13",
            is_default=True,
            is_active=True,
            layout={
                "version": 1,
                "background_color": "#FFFFFF",
                "elements": [
                    {
                        "id": "el_header_bg",
                        "type": "shape",
                        "x_mm": 0,
                        "y_mm": 0,
                        "width_mm": 30.0,
                        "height_mm": 4.5,
                        "bg_color": "#1A2B4C",
                        "border_color": None,
                        "border_width_mm": 0,
                        "corner_radius_mm": 0,
                        "z_index": 0,
                        "visible": True,
                    },
                    {
                        "id": "el_company_name",
                        "type": "text",
                        "field": "company.name",
                        "static_value": None,
                        "x_mm": 1.0,
                        "y_mm": 0.5,
                        "width_mm": 28.0,
                        "height_mm": 3.5,
                        "font_family": "Gilroy-Bold",
                        "font_size": 7.0,
                        "bold": True,
                        "italic": False,
                        "underline": False,
                        "align": "center",
                        "color": "#FFFFFF",
                        "z_index": 10,
                        "visible": True,
                    },
                    {
                        "id": "el_product_name",
                        "type": "text",
                        "field": "product.name",
                        "static_value": None,
                        "x_mm": 1.0,
                        "y_mm": 4.8,
                        "width_mm": 28.0,
                        "height_mm": 3.5,
                        "font_family": "Gilroy-Bold",
                        "font_size": 6.5,
                        "bold": True,
                        "italic": False,
                        "underline": False,
                        "align": "left",
                        "color": "#000000",
                        "z_index": 10,
                        "visible": True,
                    },
                    {
                        "id": "el_product_sku",
                        "type": "text",
                        "field": "product.sku",
                        "static_value": None,
                        "x_mm": 1.0,
                        "y_mm": 8.5,
                        "width_mm": 16.0,
                        "height_mm": 2.5,
                        "font_family": "Courier",
                        "font_size": 5.5,
                        "bold": False,
                        "italic": False,
                        "underline": False,
                        "align": "left",
                        "color": "#333333",
                        "z_index": 10,
                        "visible": True,
                    },
                    {
                        "id": "el_product_price",
                        "type": "text",
                        "field": "product.batch.selling_price",
                        "static_value": None,
                        "x_mm": 17.0,
                        "y_mm": 8.5,
                        "width_mm": 12.0,
                        "height_mm": 2.5,
                        "font_family": "Gilroy-Bold",
                        "font_size": 5.5,
                        "bold": True,
                        "italic": False,
                        "underline": False,
                        "align": "right",
                        "color": "#000000",
                        "z_index": 10,
                        "visible": True,
                    },
                    {
                        "id": "el_barcode",
                        "type": "barcode",
                        "field": "product.barcode",
                        "x_mm": 2.0,
                        "y_mm": 11.2,
                        "width_mm": 26.0,
                        "height_mm": 7.2,
                        "z_index": 10,
                        "visible": True,
                        "barcode_options": {
                            "show_text": True,
                            "font_size": 6.0,
                        },
                    },
                ],
            },
        )


def reverse_default_template(apps, schema_editor):
    BarcodeTemplate = apps.get_model("products", "BarcodeTemplate")
    BarcodeTemplate.objects.filter(name="Avtoyon Standart 30x19").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0029_brand_logo_barcodetemplate"),
    ]

    operations = [
        migrations.RunPython(seed_default_template, reverse_default_template),
    ]
