import math
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


# ─────────────────────────────────────────────
# PAGINATION
# ─────────────────────────────────────────────

class StandardPagination(PageNumberPagination):
    # ✅ YAXSHI: Default va maksimal page size mavjud, katta querysetlar nazoratsiz qaytmaydi.
    page_size = 20
    page_size_query_param = "limit"
    max_page_size = 100

    def get_paginated_response(self, data):
        page_size = self.get_page_size(self.request)
        count = self.page.paginator.count
        total_pages = math.ceil(count / page_size) if page_size else 1

        return Response({
            "count": count,
            "total_pages": total_pages,
            "current_page": self.page.number,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [Max page size sozlamasini endpoint talablariga qarab kuzatish]
# ═══════════════════════════════
