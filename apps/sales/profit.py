"""
Sof foyda hisobining YAGONA manbai.

Bu modul barcha joylarda (hisobotlar moduli, dashboard, sotuvlar statistikasi)
bir xil formula ishlatilishini kafolatlaydi — aks holda har sahifada boshqacha
raqam chiqib, foydalanuvchi qaysi biriga ishonishni bilmay qolardi.

Formula (bitta sotuv qatori — SaleItem uchun):

    sof_miqdor    = quantity - returned_quantity        # qaytarilgani chiqariladi
    yalpi_foyda   = (unit_price - purchase_price) * sof_miqdor
    chegirma_ulush = sale.discount_amount * (item_ulushi)   # chekka chegirma
    sof_foyda     = yalpi_foyda - chegirma_ulush

Chegirma chek darajasida beriladi, shuning uchun u qatorlarga summa nisbatida
taqsimlanadi: item_ulushi = (unit_price * sof_miqdor) / chegirmagacha_jami,
bu yerda chegirmagacha_jami = sale.total_amount + sale.discount_amount.

Eslatmalar:
  - purchase_price NULL (eski/import qilingan sotuvlar) bo'lsa 0 deb olinadi —
    bunday qatorlarda foyda sotuv narxiga teng bo'lib, ATAYLAB oshiq chiqadi.
    Shuning uchun `partial_cost_expr()` bilan bunday sotuvlar borligini
    aniqlab, UI'da "foyda taxminiy" ogohlantirishini ko'rsatish mumkin.
  - To'liq qaytarilgan sotuvlarda barcha qatorlarning returned_quantity =
    quantity bo'ladi, ya'ni foyda avtomatik 0 ga tushadi — status bo'yicha
    alohida filtr shart emas.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import (
    Case,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

# Pul ustunlari bilan bir xil aniqlik — oraliq bo'lishlarda yaxlitlash yo'qolmasin
_MONEY = DecimalField(max_digits=20, decimal_places=2)
_ZERO = Value(Decimal("0"), output_field=_MONEY)


def _net_qty():
    """Qaytarilganini chiqarib tashlagan sof miqdor (manfiy bo'lmaydi)."""
    return Case(
        When(quantity__gt=F("returned_quantity"), then=F("quantity") - F("returned_quantity")),
        default=Value(Decimal("0")),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def item_profit_expr(sale_path: str = "sale"):
    """
    SaleItem querysetlari uchun sof foyda ifodasi.

    sale_path — SaleItem'dan Sale'gacha bo'lgan yo'l ("sale" yoki nested holatda
    boshqacha bo'lishi mumkin).
    """
    discount = f"{sale_path}__discount_amount"
    total_after = f"{sale_path}__total_amount"

    net_qty = _net_qty()
    # Qator daromadi (chegirmagacha) — chegirma ulushini hisoblash uchun asos
    line_revenue = ExpressionWrapper(F("unit_price") * net_qty, output_field=_MONEY)
    gross = ExpressionWrapper(
        (F("unit_price") - Coalesce(F("purchase_price"), _ZERO)) * net_qty,
        output_field=_MONEY,
    )
    # Chekdagi chegirmagacha bo'lgan jami: total_amount allaqachon chegirma
    # ayirilgan holda saqlanadi, shuning uchun uni qaytarib qo'shamiz
    subtotal = ExpressionWrapper(
        Coalesce(F(total_after), _ZERO) + Coalesce(F(discount), _ZERO),
        output_field=_MONEY,
    )
    # Chegirma ulushi — subtotal 0 bo'lsa (nazariy holat) bo'linish qilinmaydi
    discount_share = Case(
        When(
            **{f"{discount}__gt": 0},
            then=ExpressionWrapper(
                Coalesce(F(discount), _ZERO) * line_revenue / subtotal,
                output_field=_MONEY,
            ),
        ),
        default=_ZERO,
        output_field=_MONEY,
    )
    return ExpressionWrapper(gross - discount_share, output_field=_MONEY)


def sum_item_profit(sale_path: str = "sale"):
    """SaleItem queryset'ida aggregate/annotate uchun: Sum(sof foyda)."""
    return Coalesce(Sum(item_profit_expr(sale_path)), _ZERO, output_field=_MONEY)


def partial_cost_filter(sale_path: str = "sale") -> Q:
    """
    Tannarxi yozilmagan (purchase_price NULL yoki 0) sotilgan qatorlar filtri.
    Bunday qatorlar bo'lsa foyda oshiq ko'rinadi — UI ogohlantirishi uchun.
    """
    return Q(purchase_price__isnull=True) | Q(purchase_price=0)
