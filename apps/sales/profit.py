"""
apps/sales/profit.py — Sotuvlar xarajatlari va tannarx yordamchilari.
"""
from __future__ import annotations

from django.db.models import Q


def partial_cost_filter(sale_path: str = "sale") -> Q:
    """
    Tannarxi yozilmagan (purchase_price NULL yoki 0) sotilgan qatorlar filtri.
    Bunday qatorlar bo'lsa foyda oshiq ko'rinadi — UI ogohlantirishi uchun.
    """
    return Q(purchase_price__isnull=True) | Q(purchase_price=0)

