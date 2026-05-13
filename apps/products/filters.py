from django_filters import rest_framework as filters

from apps.products.models import Product


# ─────────────────────────────────────────────
# FILTER
# ─────────────────────────────────────────────

class ProductFilter(filters.FilterSet):
    """
    Qo'llab-quvvatlanadigan filterlar:
      ?category=<id>         → aniq kategoriya bo'yicha
      ?is_active=true/false  → faol/nofaol mahsulotlar
    """
    # ✅ YAXSHI: FK filter `NumberFilter` orqali yozilgan, ModelChoiceFilter `.all()` yuklashidan qochilgan.
    category = filters.NumberFilter(field_name="category__id")
    is_active = filters.BooleanFilter(field_name="is_active")

    class Meta:
        model = Product
        fields = ["category", "is_active"]


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Katta katalogda `category_id` indeks mavjudligini migratsiyada tekshirish]
# ═══════════════════════════════
