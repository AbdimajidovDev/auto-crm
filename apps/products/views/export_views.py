from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.products.models import Category, Product, ProductBatch
from apps.products.services.product_query_service import (
    annotate_stock_qty,
    apply_stock_status,
    apply_token_search,
    stock_status_label,
)


@extend_schema(
    tags=["Product"],
    summary="Mahsulotlarni Excel (.xlsx) ga eksport qilish "
            "(?search=&category=&store_id=&stock_status=&date_from=&date_to=). "
            "Filtrlar ro'yxat sahifasi bilan bir xil ishlaydi: token qidiruv, "
            "do'kon kesimi va qoldiq holati (in_stock/low_stock/out_of_stock)",
)
class ProductExportAPIView(BaseExcelExportAPIView):
    filename = "mahsulotlar"
    date_field = "created_at"

    def get_queryset(self, request):
        qs = (
            Product.objects
            .filter(status=Product.ProductStatus.ACTIVE)
            .select_related("category", "brand", "unit_measurement")
            .prefetch_related(
                Prefetch(
                    "batches",
                    queryset=(
                        ProductBatch.objects
                        .filter(is_active=True)
                        .select_related("store")
                    ),
                ),
            )
            .order_by("name")
        )

        # Ro'yxat sahifasi bilan BIR XIL filtr logikasi (product_query_service)
        qs = apply_token_search(qs, request.query_params.get("search"))

        category = request.query_params.get("category")
        if category and category.isdigit():
            qs = qs.filter(category_id=int(category))

        # Do'kon kesimi: faqat shu do'konda mavjud mahsulotlar, stock_qty ham shu
        # do'kon bo'yicha; qoldiq holati filtri shu qiymatga tayanadi
        store_id = request.query_params.get("store_id")
        store_id = store_id if store_id and store_id.isdigit() else None
        qs = annotate_stock_qty(qs, store_id)
        qs = apply_stock_status(qs, request.query_params.get("stock_status"))

        return qs

    def get_sheets(self, request, queryset):
        store_id = request.query_params.get("store_id")
        store_id = int(store_id) if store_id and store_id.isdigit() else None

        columns = [
            ("ID", 8),
            ("Nomi", 40),
            ("SKU", 14),
            ("Shtrixkod", 16),
            ("Kategoriya", 22),
            ("Brend", 16),
            ("O'lchov", 10),
            ("Qoldiq" if store_id else "Jami qoldiq", 12),
            ("Holat", 12),
            ("Min zaxira", 12),
            ("Yaratilgan", 17),
        ]

        def rows():
            for product in queryset:
                batches = [
                    b for b in product.batches.all()
                    if store_id is None or b.store_id == store_id
                ]
                qty = sum(b.quantity for b in batches)
                yield [
                    product.id,
                    product.name,
                    product.sku,
                    product.barcode,
                    product.category.name if product.category else "",
                    product.brand.name if product.brand else "",
                    product.unit_measurement.measurement if product.unit_measurement else "",
                    qty,
                    stock_status_label(qty),
                    product.min_stock,
                    product.created_at,
                ]

        return [("Mahsulotlar", columns, rows())]


@extend_schema(
    tags=["Category"],
    summary="Kategoriyalarni Excel (.xlsx) ga eksport qilish (?search=&date_from=&date_to=)",
)
class CategoryExportAPIView(BaseExcelExportAPIView):
    filename = "kategoriyalar"
    date_field = "created_at"

    def get_queryset(self, request):
        qs = Category.objects.annotate(products_count=Count("product")).order_by("name")
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        return qs

    def get_sheets(self, request, queryset):
        columns = [
            ("ID", 8),
            ("Nomi", 30),
            ("Tavsif", 40),
            ("Mahsulotlar soni", 16),
            ("Yaratilgan", 17),
        ]

        def rows():
            for category in queryset:
                yield [
                    category.id,
                    category.name,
                    category.description,
                    category.products_count,
                    category.created_at,
                ]

        return [("Kategoriyalar", columns, rows())]
