"""
Qoldiqning O'TMISHDAGI holati (as-of hisobot).

Tizimda umumiy "ombor harakatlari" ledgeri yo'q — qoldiq `ProductBatch.quantity`
da faqat JORIY holat bo'lib turadi. Shuning uchun tanlangan sanadagi qoldiq
joriy qoldiqdan ORQAGA qarab tiklanadi:

    qoldiq(D) = joriy qoldiq − (D dan keyin sodir bo'lgan harakatlar sof ta'siri)

Nega shunday, "boshidan D gacha hujjatlarni qo'shish" emas:
  • joriy qoldiq — haqiqiy fakt (inventarizatsiya to'g'rilashlari ham unda),
    hujjatlardan qayta yig'ilgan qiymat esa har qanday hujjatsiz to'g'rilashda
    haqiqatdan uzoqlashadi;
  • D dan keyingi harakatlar odatda butun tarixdan ancha kam — tezroq.

Hisobga olinadigan harakatlar (hammasi qoldiqni o'zgartiradigan yagona yo'llar):
  +  kirim               StockEntryItem       (entry.store, entry.created_at)
  −  sotuv               SaleItem             (sale.store, sale.created_at)
  +  sotuvdan qaytarish   SaleReturnItem       (sale_return.store, created_at)
  −  ta'minotchiga qaytim StockEntryReturnItem (entry.store, stock_return.created_at)
  −  spisaniye            WriteOffItem         (write_off.store, created_at)
  ∓  transfer             StockTransferItem    (from_store −, to_store +, approved_at)

Ma'lum cheklovlar:
  • Inventarizatsiya ORTIQCHASI (sanoqda ko'p chiqqan) hujjat yozmaydi —
    `InventoryService.finalize()` qoldiqni to'g'ridan-to'g'ri o'rnatadi. Kamomad
    esa WriteOff bilan yoziladi va to'g'ri teskari qilinadi. Ortiqcha faqat
    inventarizatsiya sanasidan OLDINGI hisobotlarda farq berishi mumkin.
  • Arxivdan butunlay o'chirilgan (30 kundan oshgan) sotuvlar hisobga olinmaydi —
    ularning qatorlari bazada qolmaydi.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Q, Sum
from django.utils import timezone

from apps.contract.models import StockEntryItem, StockEntryReturnItem
from apps.sales.models import SaleItem, SaleReturnItem
from apps.transfer.models import StockTransfer, StockTransferItem
from apps.writeoff.models import WriteOffItem


def day_end(day: date) -> datetime:
    """Tanlangan kunning oxiri = ertangi kunning 00:00 (yarim-ochiq chegara)."""
    moment = datetime.combine(day + timedelta(days=1), time.min)
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, timezone.get_current_timezone())
    return moment


def _store_filter(store_id, field: str) -> Q:
    return Q(**{field: store_id}) if store_id else Q()


def _accumulate(acc: dict, rows, store_field: str, sign: int) -> None:
    for r in rows:
        key = (r[store_field], r["product_id"])
        acc[key] = acc.get(key, 0) + sign * (r["moved"] or 0)


def stock_delta_after(cutoff: datetime, store_id=None) -> dict[tuple[int, int], int]:
    """
    (store_id, product_id) → `cutoff` dan KEYIN qoldiqqa qo'shilgan SOF miqdor.

    Musbat — qoldiq oshgan, manfiy — kamaygan. Sanadagi qoldiqni olish uchun
    joriy qoldiqdan shu qiymat AYIRILADI.

    Har harakat turi uchun bitta GROUP BY so'rov — jami 7 ta so'rov.
    """
    delta: dict[tuple[int, int], int] = {}

    # Kirim (+)
    _accumulate(
        delta,
        StockEntryItem.objects
        .filter(entry__created_at__gte=cutoff)
        .filter(_store_filter(store_id, "entry__store_id"))
        .values("entry__store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "entry__store_id", +1,
    )

    # Sotuv (−). Arxivlangan (soft-delete) sotuvlar ham kiradi: o'chirish
    # faqat hisobotdan yashiradi, ombordan yechilgan tovarni qaytarmaydi.
    _accumulate(
        delta,
        SaleItem.objects
        .filter(sale__created_at__gte=cutoff)
        .filter(_store_filter(store_id, "sale__store_id"))
        .values("sale__store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "sale__store_id", -1,
    )

    # Sotuvdan qaytarish (+) — SaleItem.returned_quantity emas, alohida hujjat
    # sanasi bo'yicha (qaytarish sotuvdan keyingi boshqa kunda bo'lishi mumkin)
    _accumulate(
        delta,
        SaleReturnItem.objects
        .filter(sale_return__created_at__gte=cutoff)
        .filter(_store_filter(store_id, "sale_return__store_id"))
        .values("sale_return__store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "sale_return__store_id", +1,
    )

    # Ta'minotchiga qaytarish (−)
    _accumulate(
        delta,
        StockEntryReturnItem.objects
        .filter(stock_return__created_at__gte=cutoff)
        .filter(_store_filter(store_id, "stock_return__entry__store_id"))
        .values("stock_return__entry__store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "stock_return__entry__store_id", -1,
    )

    # Spisaniye (−). Inventarizatsiya kamomadi ham shu yerda yoziladi
    # (record-only bo'lsa ham qoldiq finalize'da aynan shuncha kamaytirilgan).
    _accumulate(
        delta,
        WriteOffItem.objects
        .filter(write_off__created_at__gte=cutoff)
        .filter(_store_filter(store_id, "write_off__store_id"))
        .values("write_off__store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "write_off__store_id", -1,
    )

    # Transfer — qoldiq FAQAT tasdiqlanganda ko'chadi (approved_at).
    # Eski yozuvlarda approved_at bo'sh qolgan bo'lsa created_at ga tayanamiz.
    approved = (
        StockTransferItem.objects
        .filter(stock_transfer__status=StockTransfer.Status.APPROVED)
        .filter(
            Q(stock_transfer__approved_at__gte=cutoff)
            | Q(
                stock_transfer__approved_at__isnull=True,
                stock_transfer__created_at__gte=cutoff,
            )
        )
    )
    _accumulate(
        delta,
        approved
        .filter(_store_filter(store_id, "stock_transfer__from_store_id"))
        .values("stock_transfer__from_store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "stock_transfer__from_store_id", -1,
    )
    _accumulate(
        delta,
        approved
        .filter(_store_filter(store_id, "stock_transfer__to_store_id"))
        .values("stock_transfer__to_store_id", "product_id")
        .annotate(moved=Sum("quantity")),
        "stock_transfer__to_store_id", +1,
    )

    return delta
