from django_filters import rest_framework as filters

from apps.products.models import Product


# ─────────────────────────────────────────────
# FILTER
# ─────────────────────────────────────────────

# Bir so'rovda ko'pi bilan shuncha ID qabul qilinadi — mijoz ro'yxatni o'zi
# bo'laklarga bo'lib yuboradi, bu juda uzun URL va og'ir IN (...) dan himoya
MAX_ID_FILTER = 200


class ProductFilter(filters.FilterSet):
    """
    Qo'llab-quvvatlanadigan filterlar:
      ?category=<id>         → aniq kategoriya bo'yicha
      ?is_active=true/false  → faol/nofaol mahsulotlar
      ?ids=1,2,3             → aniq ID'lar bo'yicha
    """
    # ✅ YAXSHI: FK filter `NumberFilter` orqali yozilgan, ModelChoiceFilter `.all()` yuklashidan qochilgan.
    category = filters.NumberFilter(field_name="category__id")
    is_active = filters.BooleanFilter(field_name="is_active")
    # Katalog konteksti mijozda faqat bir sahifa (100 ta) mahsulot saqlaydi.
    # Qoralamada yoki Excel importida undan tashqaridagi tovar bo'lsa, uni
    # ID bo'yicha BITTA so'rov bilan olib kelish uchun shu filter kerak.
    ids = filters.CharFilter(method="filter_ids")

    class Meta:
        model = Product
        fields = ["category", "is_active"]

    @staticmethod
    def filter_ids(queryset, name, value):
        # django-filter bo'sh qiymatda metodni umuman chaqirmaydi, ya'ni
        # ?ids= bo'lmasa ro'yxat odatdagidek to'liq qaytadi
        ids = [chunk.strip() for chunk in str(value).split(",")]
        ids = [int(chunk) for chunk in ids if chunk.isdigit()][:MAX_ID_FILTER]
        if not ids:
            # Faqat yaroqsiz ID yuborilgan — butun katalogni qaytarish
            # so'ralganidan butunlay boshqa natija bo'lardi
            return queryset.none()
        return queryset.filter(id__in=ids)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Katta katalogda `category_id` indeks mavjudligini migratsiyada tekshirish]
# ═══════════════════════════════
