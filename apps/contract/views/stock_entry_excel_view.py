"""
stock_entry_excel_view.py — Excel orqali omborga KIRIM API'lari.

  POST /contract/entry/import/           — Excel fayldan kirim yaratish
  GET  /contract/entry/import/template/  — kirim shablonini yuklab olish

Kirim faqat asosiy do'konga (Store.type='b') qilinadi.
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import FileResponse

from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsSuperUser
from apps.store.models import Store
from apps.contract.serializers import StockEntryImportSerializer
from apps.contract.services.stock_entry_import_service import StockEntryImportService


TEMPLATE_PATH = os.path.join(
    settings.BASE_DIR,
    "core",
    "templates",
    "kirim_shablon.xlsx",
)


@extend_schema(
    tags=["Stock Entry"],
    summary="Excel orqali omborga kirim qilish",
    request={
        "multipart/form-data": {
            "type": "object",
            "properties": {
                "supplier": {"type": "integer", "description": "Yetkazib beruvchi ID (majburiy)"},
                "cash_amount": {"type": "string", "description": "Naqd to'lov (ixtiyoriy, default 0)"},
                "card_amount": {"type": "string", "description": "Karta to'lovi (ixtiyoriy, default 0)"},
                "file": {"type": "string", "format": "binary", "description": "Kirim Excel fayli (.xlsx)"},
            },
            "required": ["supplier", "file"],
        }
    },
    responses={201: OpenApiTypes.OBJECT},
)
class StockEntryImportAPIView(APIView):
    permission_classes = [IsSuperUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file maydoni majburiy."}, status=400)
        if not file.name.endswith(".xlsx"):
            return Response({"detail": "Faqat .xlsx fayl qabul qilinadi."}, status=400)

        serializer = StockEntryImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        store, store_error = self._resolve_base_store()
        if store_error:
            return Response({"detail": store_error}, status=400)

        try:
            result = StockEntryImportService.import_from_excel(
                file=file,
                supplier=data["supplier"],
                store=store,
                cash_amount=data["cash_amount"],
                card_amount=data["card_amount"],
                user=request.user,
            )
        except ValidationError as e:
            return Response({"detail": e.messages[0] if hasattr(e, "messages") else str(e)}, status=400)

        if result["entry_id"] is None:
            # Hech bir satr import qilinmadi — sabablar skipped da
            return Response(
                {"detail": "Hech qanday yaroqli satr topilmadi, kirim yaratilmadi.", **result},
                status=400,
            )

        return Response(result, status=201)

    @staticmethod
    def _resolve_base_store():
        """Kirim har doim asosiy do'konga (type='b') qilinadi — avtomatik aniqlanadi."""
        base_qs = Store.objects.filter(is_active=True, type=Store.StoreType.BASE)
        count = base_qs.count()
        if count == 0:
            return None, "Asosiy do'kon (type='b') topilmadi."
        if count > 1:
            return None, "Bir nechta faol asosiy do'kon mavjud — sozlamalarda bittasini qoldiring."
        return base_qs.first(), None


@extend_schema(
    tags=["Stock Entry"],
    summary="Kirim import shablonini yuklab olish",
)
class StockEntryImportTemplateAPIView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request):
        if not os.path.exists(TEMPLATE_PATH):
            return Response({"detail": "Shablon fayl topilmadi."}, status=404)

        return FileResponse(
            open(TEMPLATE_PATH, "rb"),
            as_attachment=True,
            filename="kirim_shablon.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )