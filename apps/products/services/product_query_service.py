"""
Mahsulotlar ro'yxati (list API) va Excel eksporti uchun UMUMIY filtr logikasi.

Ikkala view ham aynan shu funksiyalarni ishlatadi — sahifada ko'ringan
natija bilan eksport qilingan fayl doim bir xil bo'lishi kafolatlanadi.
"""

from django.db.models import Exists, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce

from apps.products.models import ProductBatch

# Frontend bilan kelishilgan qoldiq chegarasi: 0 < qty <= 5 → "kam qolgan"
LOW_STOCK_THRESHOLD = 5


def apply_token_search(queryset, search):
    """
    Token qidiruv: har bir so'z alohida (AND) tekshiriladi — so'zlar tartibi
    ahamiyatsiz ("cobalt dvornik" = "dvornik cobalt"). Har token nomning
    lotin/kirill variantlari, SKU, barcode va tavsif bo'yicha izlanadi;
    raqamli token ID ga ham mos kelishi mumkin.
    """
    search = (search or "").strip()
    if not search:
        return queryset
    for token in search.split():
        token_q = (
            Q(name_uz__icontains=token)
            | Q(name_uz_cyrl__icontains=token)
            | Q(sku__icontains=token)
            | Q(barcode__icontains=token)
            | Q(description__icontains=token)
        )
        if token.isdigit():
            token_q |= Q(id=int(token))
        queryset = queryset.filter(token_q)
    return queryset


def annotate_stock_qty(queryset, store_id=None):
    """
    stock_qty annotatsiyasi — stock_status filtri va stats uchun asos.

    store_id berilsa: qoldiq faqat shu do'kon bo'yicha va ro'yxatda faqat shu
    do'konda (faol batch bilan) mavjud mahsulotlar qoladi; bo'lmasa barcha
    do'konlar jami. Subquery ishlatiladi: tashqi filter'da batches JOIN'i
    takrorlansa Sum noto'g'ri (ko'paygan) qiymat berishi mumkin.
    """
    if store_id:
        batches = ProductBatch.objects.filter(
            product_id=OuterRef("pk"),
            store_id=int(store_id),
            is_active=True,
        )
        return (
            queryset
            .annotate(
                stock_qty=Coalesce(
                    Subquery(
                        batches
                        .values("product_id")
                        .annotate(total=Sum("quantity"))
                        .values("total")[:1]
                    ),
                    0,
                )
            )
            .filter(Exists(batches))
        )

    all_batches = ProductBatch.objects.filter(
        product_id=OuterRef("pk"),
        is_active=True,
    )
    return queryset.annotate(
        stock_qty=Coalesce(
            Subquery(
                all_batches
                .values("product_id")
                .annotate(total=Sum("quantity"))
                .values("total")[:1]
            ),
            0,
        )
    )


def apply_stock_status(queryset, stock_status):
    """stock_qty annotatsiyasi asosida qoldiq holati bo'yicha filtrlaydi."""
    if stock_status == "out_of_stock":
        return queryset.filter(stock_qty__lte=0)
    if stock_status == "low_stock":
        return queryset.filter(stock_qty__gt=0, stock_qty__lte=LOW_STOCK_THRESHOLD)
    if stock_status == "in_stock":
        return queryset.filter(stock_qty__gt=LOW_STOCK_THRESHOLD)
    return queryset


def stock_status_label(qty) -> str:
    """Excel/hisobotlar uchun qoldiq holati matni."""
    qty = qty or 0
    if qty <= 0:
        return "Tugagan"
    if qty <= LOW_STOCK_THRESHOLD:
        return "Kam qolgan"
    return "Bor"
