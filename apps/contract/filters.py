from django.db.models import F, Q
from django_filters import rest_framework as filters

from apps.contract.models import StockEntry


# ─────────────────────────────────────────────
# FILTER
# ─────────────────────────────────────────────

class StockEntryFilter(filters.FilterSet):
    """
    ?supplier=<id>         → yetkazib beruvchi bo'yicha
    ?store=<id>            → do'kon bo'yicha
    ?date_from=2024-01-01  → sana oralig'i boshi
    ?date_to=2024-12-31    → sana oralig'i oxiri
    ?payment_status=       → unpaid | partial | paid
                             (view'dagi total_in/total_paid annotatsiyalariga tayanadi)

    NumberFilter ishlatildi — FK string ko'rinishida bo'lsa ham
    django-filters xatosi chiqmaydi (avvalgi SaleFilter xatosi takrorlanmaydi).
    """
    # ✅ YAXSHI: FK filterlar `NumberFilter` bilan yozilgan, queryset `.all()` orqali form variantlarini yuklamaydi.
    supplier = filters.NumberFilter(field_name="supplier__id")
    store = filters.NumberFilter(field_name="store__id")
    date_from = filters.DateFilter(field_name="created_at__date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="created_at__date", lookup_expr="lte")
    payment_status = filters.CharFilter(method="filter_payment_status")

    class Meta:
        model = StockEntry
        fields = ["supplier", "store", "date_from", "date_to", "payment_status"]

    def filter_payment_status(self, queryset, name, value):
        # "in" tranzaksiyasi faqat qarz qismini saqlaydi: qarz = total_in - total_paid,
        # kirim paytida to'langani esa paid_amount ustunida. Shunga mos holatlar:
        if value == "unpaid":
            return queryset.filter(total_in__gt=F("total_paid"), total_paid__lte=0, paid_amount__lte=0)
        if value == "partial":
            return queryset.filter(total_in__gt=F("total_paid")).filter(
                Q(total_paid__gt=0) | Q(paid_amount__gt=0)
            )
        if value == "paid":
            return queryset.filter(total_paid__gte=F("total_in"))
        return queryset


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [created_at/supplier/store bo'yicha indekslarni migrationlarda tekshirish]
# ═══════════════════════════════
