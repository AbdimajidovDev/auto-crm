# api/top_products.py
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.reports.services.top_product_service import TopProductsService
from apps.reports.utils.date_filters import DateRangeResolver
from apps.reports.utils.date_parser import DateValidator


@extend_schema(
    tags=['Dashboard'],
    summary="Eng ko'p sotilgan mahsulotlar.",
)
class TopProductsAPIView(APIView):
    # ⚠️ MUAMMO [ARXITEKTURA]: permission_classes berilmagan. Service ichida request.user'ga
    # (is_superuser, store scope) tayaniladi, lekin view autentifikatsiyani majburlamaydi —
    # DEFAULT_PERMISSION_CLASSES noto'g'ri bo'lsa anonim foydalanuvchi ham murojaat qila oladi.
    # ✅ YECHIM: permission_classes = [IsAuthenticated]

    def get(self, request):

        filter_type = request.GET.get("filter", "daily")
        # ⚠️ MUAMMO [PERF]: limit foydalanuvchi kiritgan qiymatdan to'g'ridan-to'g'ri int()'ga
        # o'giriladi — (1) yuqori chegara yo'q, ?limit=100000 bo'lsa SaleItem(65k) bo'yicha
        # og'ir aggregate qatorlarini cheklamay qaytaradi; (2) noto'g'ri qiymatda (?limit=abc)
        # ValueError → 500 (400 emas).
        # ✅ YECHIM: limit = min(int(request.GET.get("limit", 5) or 5), 50)  # try/except bilan
        limit = int(request.GET.get("limit", 5))
        store_id = request.GET.get("store_id")

        from_date, to_date = DateValidator.validate(
            request.GET.get("from"),
            request.GET.get("to")
        )

        if not from_date:
            from_date, to_date = DateRangeResolver.resolve(filter_type)

        data = TopProductsService.get_top_products(
            user=request.user,
            date_from=from_date,
            date_to=to_date,
            limit=limit,
            store_id=store_id
        )

        return Response({
            "topProducts": data
        })


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 1
# Umumiy baho: 5 / 10
# Prioritet: [permission_classes=[IsAuthenticated] va limit min(...,50) bilan cheklash]
# ═══════════════════════════════
