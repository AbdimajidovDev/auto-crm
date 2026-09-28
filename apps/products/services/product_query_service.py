"""
Mahsulotlar ro'yxati (list API) va Excel eksporti uchun UMUMIY filtr logikasi.

Ikkala view ham aynan shu funksiyalarni ishlatadi — sahifada ko'ringan
natija bilan eksport qilingan fayl doim bir xil bo'lishi kafolatlanadi.
"""

from django.db.models import (
    Case,
    DecimalField,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

from apps.products.models import ProductBatch

# Frontend bilan kelishilgan qoldiq chegarasi: 0 < qty <= 5 → "kam qolgan"
LOW_STOCK_THRESHOLD = 5

# Kod bo'yicha mos kelmagan (ya'ni nom/tavsif bo'yicha topilgan) yozuvlar darajasi.
# Kichik raqam = ro'yxatda yuqori.
SEARCH_RANK_OTHER = 9


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


def apply_search_rank(queryset, search):
    """
    Qidiruv natijalarini moslik turi bo'yicha darajalaydi (`search_rank`).

    Tartib: artikul (SKU) birlamchi, shtrix kod (barcode) ikkilamchi, qolgani
    (nom/tavsif bo'yicha topilganlar) oxirida. Har guruh ichida aniq moslik →
    boshidan moslik → oxiridan moslik → ichidan moslik. Oxiridan moslik alohida
    ajratilgan, chunki artikul "prefiks + raqam" ko'rinishida (A00544) — raqam
    qismini to'liq yozgan odam aynan shu mahsulotni izlaydi.

    Masalan "00544" yozilsa avval artikuli A00544 bo'lgan mahsulot, keyin
    artikulida 00544 uchraydiganlar, undan keyin shtrix kodi bo'yicha mos
    kelganlar chiqadi.

    Bir nechta so'z yozilganda (artikul/shtrix kodda probel bo'lmaydi) barcha
    yozuv bir xil darajaga tushadi — nom bo'yicha qidiruv tartibi o'zgarmaydi.
    """
    search = (search or "").strip()
    if not search:
        return queryset
    return queryset.annotate(
        search_rank=Case(
            When(sku__iexact=search, then=Value(0)),
            When(sku__istartswith=search, then=Value(1)),
            When(sku__iendswith=search, then=Value(2)),
            When(sku__icontains=search, then=Value(3)),
            When(barcode__iexact=search, then=Value(4)),
            When(barcode__istartswith=search, then=Value(5)),
            When(barcode__iendswith=search, then=Value(6)),
            When(barcode__icontains=search, then=Value(7)),
            default=Value(SEARCH_RANK_OTHER),
            output_field=IntegerField(),
        )
    )


def annotate_stock_qty(queryset, store_id=None, only_in_store=True):
    """
    stock_qty annotatsiyasi — stock_status filtri va stats uchun asos.

    store_id berilsa: qoldiq faqat shu do'kon bo'yicha va (only_in_store=True
    bo'lsa) ro'yxatda faqat shu do'konda faol batch bilan mavjud mahsulotlar
    qoladi. only_in_store=False — butun katalog qoladi, do'konda yo'q
    mahsulotlar stock_qty=0 bilan (POS katalogi shu rejimda sahifalaydi).
    store_id bo'lmasa barcha do'konlar jami. Subquery ishlatiladi: tashqi
    filter'da batches JOIN'i takrorlansa Sum noto'g'ri qiymat berishi mumkin.
    """
    if store_id:
        batches = ProductBatch.objects.filter(
            product_id=OuterRef("pk"),
            store_id=int(store_id),
            is_active=True,
        )
        # output_field majburiy: quantity endi Decimal (juft mahsulotda 0.5 qadam),
        # 0 esa butun son — aralash tiplarda Django FieldError beradi
        annotated = queryset.annotate(
            stock_qty=Coalesce(
                Subquery(
                    batches
                    .values("product_id")
                    .annotate(total=Sum("quantity"))
                    .values("total")[:1]
                ),
                0,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        if only_in_store:
            annotated = annotated.filter(Exists(batches))
        return annotated

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
            output_field=DecimalField(max_digits=12, decimal_places=2),
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


def annotate_latest_selling_price(queryset, store_id=None):
    """
    Mahsulotning eng oxirgi kirim qilingan sotuv narxini (selling_price) aniqlab annotatsiya qiladi.

    Semantika:
    1. store_id berilsa:
       shu do'kondagi eng oxirgi kirim (StockEntryItem yoki tasdiqlangan StockTransferItem)
       bo'yicha selling_price aniqlanadi. Agar hech qaysi bo'lmasa, shu do'kon ProductBatch
       selling_price olinadi.
    2. store_id berilmasa ("Barcha do'konlar"):
       barcha do'konlar bo'yicha eng oxirgi global kirim (StockEntryItem yoki tasdiqlangan
       StockTransferItem) bo'yicha selling_price aniqlanadi. Agar hech qaysi kirim bo'lmasa,
       eng oxirgi yangilangan faol ProductBatch narxi olinadi.
    """
    from apps.contract.models import StockEntryItem
    from apps.transfer.models import StockTransferItem

    target_store_id = int(store_id) if store_id is not None and str(store_id).isdigit() else None

    if target_store_id:
        e_price = Subquery(
            StockEntryItem.objects.filter(
                product_id=OuterRef("pk"),
                entry__store_id=target_store_id,
                selling_price__gt=0,
            ).order_by("-entry__created_at", "-id").values("selling_price")[:1]
        )
        e_time = Subquery(
            StockEntryItem.objects.filter(
                product_id=OuterRef("pk"),
                entry__store_id=target_store_id,
                selling_price__gt=0,
            ).order_by("-entry__created_at", "-id").values("entry__created_at")[:1]
        )
        t_price = Subquery(
            StockTransferItem.objects.filter(
                product_id=OuterRef("pk"),
                stock_transfer__to_store_id=target_store_id,
                stock_transfer__status="a",
                selling_price__gt=0,
            )
            .annotate(
                t_effective_time=Coalesce("stock_transfer__approved_at", "stock_transfer__created_at")
            )
            .order_by("-t_effective_time", "-id")
            .values("selling_price")[:1]
        )
        t_time = Subquery(
            StockTransferItem.objects.filter(
                product_id=OuterRef("pk"),
                stock_transfer__to_store_id=target_store_id,
                stock_transfer__status="a",
                selling_price__gt=0,
            )
            .annotate(
                t_effective_time=Coalesce("stock_transfer__approved_at", "stock_transfer__created_at")
            )
            .order_by("-t_effective_time", "-id")
            .values("t_effective_time")[:1]
        )
        b_price = Subquery(
            ProductBatch.objects.filter(
                product_id=OuterRef("pk"),
                store_id=target_store_id,
                is_active=True,
                selling_price__gt=0,
            ).values("selling_price")[:1]
        )
    else:
        e_price = Subquery(
            StockEntryItem.objects.filter(
                product_id=OuterRef("pk"),
                selling_price__gt=0,
            ).order_by("-entry__created_at", "-id").values("selling_price")[:1]
        )
        e_time = Subquery(
            StockEntryItem.objects.filter(
                product_id=OuterRef("pk"),
                selling_price__gt=0,
            ).order_by("-entry__created_at", "-id").values("entry__created_at")[:1]
        )
        t_price = Subquery(
            StockTransferItem.objects.filter(
                product_id=OuterRef("pk"),
                stock_transfer__status="a",
                selling_price__gt=0,
            )
            .annotate(
                t_effective_time=Coalesce("stock_transfer__approved_at", "stock_transfer__created_at")
            )
            .order_by("-t_effective_time", "-id")
            .values("selling_price")[:1]
        )
        t_time = Subquery(
            StockTransferItem.objects.filter(
                product_id=OuterRef("pk"),
                stock_transfer__status="a",
                selling_price__gt=0,
            )
            .annotate(
                t_effective_time=Coalesce("stock_transfer__approved_at", "stock_transfer__created_at")
            )
            .order_by("-t_effective_time", "-id")
            .values("t_effective_time")[:1]
        )
        b_price = Subquery(
            ProductBatch.objects.filter(
                product_id=OuterRef("pk"),
                is_active=True,
                selling_price__gt=0,
            ).order_by("-updated_at", "-id").values("selling_price")[:1]
        )

    return queryset.annotate(
        _latest_e_price=e_price,
        _latest_e_time=e_time,
        _latest_t_price=t_price,
        _latest_t_time=t_time,
        _latest_b_price=b_price,
    ).annotate(
        latest_selling_price=Case(
            When(_latest_t_time__isnull=False, _latest_e_time__isnull=False, _latest_t_time__gt=F("_latest_e_time"), then=F("_latest_t_price")),
            When(_latest_e_price__isnull=False, then=F("_latest_e_price")),
            When(_latest_t_price__isnull=False, then=F("_latest_t_price")),
            When(_latest_b_price__isnull=False, then=F("_latest_b_price")),
            default=Value(None, output_field=DecimalField(max_digits=12, decimal_places=2)),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )

