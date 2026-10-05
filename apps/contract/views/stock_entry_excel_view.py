"""
stock_entry_excel_view.py — Excel orqali omborga KIRIM API'lari.

  POST /contract/entry/import/           — Excel fayldan kirim yaratish
  POST /contract/entry/import/analyze/   — faylni import qilmasdan tahlil qilish
                                           (bazada yo'q mahsulotlarni aniqlaydi)
  GET  /contract/entry/import/template/  — kirim shablonini yuklab olish

Kirim tanlangan do'konga qilinadi; do'kon berilmasa asosiy do'kon (Store.type='b')
avtomatik aniqlanadi (eski mijozlar bilan moslik uchun).

Yangi mahsulotlar oqimi: frontend avval analyze ni chaqiradi; yangi mahsulotlar
bo'lsa foydalanuvchidan so'raydi va import ni create_products=true/false bilan
yuboradi.
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


def _extract_xlsx(request):
    """request.FILES dan .xlsx faylni oladi. Qaytaradi: (file, None) yoki (None, Response)."""
    file = request.FILES.get("file")
    if not file:
        return None, Response({"detail": "file maydoni majburiy."}, status=400)
    if not file.name.endswith(".xlsx"):
        return None, Response({"detail": "Faqat .xlsx fayl qabul qilinadi."}, status=400)
    return file, None


@extend_schema(
    tags=["Stock Entry"],
    summary="Excel orqali omborga kirim qilish",
    request={
        "multipart/form-data": {
            "type": "object",
            "properties": {
                "supplier": {"type": "integer", "description": "Yetkazib beruvchi ID (majburiy)"},
                "store": {"type": "integer", "description": "Do'kon ID (ixtiyoriy — berilmasa asosiy do'kon type='b' olinadi)"},
                "cash_amount": {"type": "string", "description": "Naqd to'lov (ixtiyoriy, default 0)"},
                "card_amount": {"type": "string", "description": "Karta to'lovi (ixtiyoriy, default 0)"},
                "create_products": {"type": "boolean", "description": "Bazada yo'q mahsulotlarni yaratib kirim qilish (default false — bunday satrlar o'tkazib yuboriladi)"},
                "file": {"type": "string", "format": "binary", "description": "Kirim Excel fayli (.xlsx)"},
            },
            "required": ["supplier", "file"],
        }
    },
    responses={201: OpenApiTypes.OBJECT},
)
def _async_stock_entry_import_worker(job, supplier_id, store_id, cash_amount, card_amount, user_id, create_products):
    from decimal import Decimal
    from apps.contract.models import Supplier
    from apps.store.models import Store
    from apps.users.models import User

    supplier = Supplier.objects.get(id=supplier_id)
    store = Store.objects.get(id=store_id)
    user = User.objects.get(id=user_id)
    with job.input_file.open("rb") as f:
        return StockEntryImportService.import_from_excel(
            file=f,
            supplier=supplier,
            store=store,
            cash_amount=Decimal(str(cash_amount or "0")),
            card_amount=Decimal(str(card_amount or "0")),
            user=user,
            create_products=create_products,
            max_rows=None,
        )


@extend_schema(
    tags=["Stock Entry"],
    summary="Excel orqali omborga kirim qilish",
    request={
        "multipart/form-data": {
            "type": "object",
            "properties": {
                "supplier": {"type": "integer", "description": "Yetkazib beruvchi ID (majburiy)"},
                "store": {"type": "integer", "description": "Do'kon ID (ixtiyoriy — berilmasa asosiy do'kon type='b' olinadi)"},
                "cash_amount": {"type": "string", "description": "Naqd to'lov (ixtiyoriy, default 0)"},
                "card_amount": {"type": "string", "description": "Karta to'lovi (ixtiyoriy, default 0)"},
                "create_products": {"type": "boolean", "description": "Bazada yo'q mahsulotlarni yaratib kirim qilish (default false — bunday satrlar o'tkazib yuboriladi)"},
                "file": {"type": "string", "format": "binary", "description": "Kirim Excel fayli (.xlsx)"},
                "async": {"type": "boolean", "description": "Fon rejimida qayta ishlash (katta fayllar uchun tavsiya etiladi)"},
            },
            "required": ["supplier", "file"],
        }
    },
    responses={201: OpenApiTypes.OBJECT, 202: OpenApiTypes.OBJECT},
)
class StockEntryImportAPIView(APIView):
    permission_classes = [IsSuperUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file, file_error = _extract_xlsx(request)
        if file_error:
            return file_error

        # File size check
        if file.size > StockEntryImportService.MAX_FILE_SIZE:
            return Response(
                {"detail": f"Fayl hajmi {StockEntryImportService.MAX_FILE_SIZE // (1024*1024)}MB dan oshmasligi kerak."},
                status=400,
            )

        serializer = StockEntryImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Do'kon so'rovda tanlangan bo'lsa — shu do'konga kirim qilinadi,
        # aks holda (eski mijozlar) asosiy do'kon avtomatik aniqlanadi.
        store = data.get("store")
        if store is None:
            store, store_error = self._resolve_base_store()
            if store_error:
                return Response({"detail": store_error}, status=400)

        # Check async mode requested
        is_async = (
            request.query_params.get("async") in ("true", "1")
            or request.headers.get("X-Async") in ("true", "1")
            or str(request.data.get("async", "")).lower() in ("true", "1")
        )

        if is_async:
            from apps.common.models import AsyncJob
            from apps.common.services.async_job_service import AsyncJobService
            job = AsyncJobService.create_and_submit(
                job_type=AsyncJob.JobType.STOCK_ENTRY_IMPORT,
                user=request.user,
                store=store,
                payload={
                    "supplier_id": data["supplier"].id,
                    "store_id": store.id,
                    "cash_amount": str(data["cash_amount"]),
                    "card_amount": str(data["card_amount"]),
                    "create_products": data["create_products"],
                    "filename": file.name,
                },
                input_file=file,
                task_func=_async_stock_entry_import_worker,
                task_args=(
                    data["supplier"].id,
                    store.id,
                    str(data["cash_amount"]),
                    str(data["card_amount"]),
                    request.user.id,
                    data["create_products"],
                ),
            )
            return Response(
                {
                    "job_id": str(job.id),
                    "status": job.status,
                    "message": "Kirim fayli fon rejimida qayta ishlanmoqda. Holatni /api/jobs/<job_id>/status/ orqali kuzatib boring.",
                    "status_url": f"/api/jobs/{job.id}/status/",
                },
                status=202,
            )

        try:
            result = StockEntryImportService.import_from_excel(
                file=file,
                supplier=data["supplier"],
                store=store,
                cash_amount=data["cash_amount"],
                card_amount=data["card_amount"],
                user=request.user,
                create_products=data["create_products"],
                max_rows=StockEntryImportService.MAX_SYNC_ROWS,
            )
        except ValidationError as e:
            return Response({"detail": e.messages[0] if hasattr(e, "messages") else str(e)}, status=400)

        if result["entry_id"] is None:
            # Hech bir satr import qilinmadi — sabablar skipped da
            return Response(
                {"detail": "Hech qanday yaroqli satr topilmadi, xarid yaratilmadi.", **result},
                status=400,
            )

        return Response(result, status=201)

    @staticmethod
    def _resolve_base_store():
        """Do'kon tanlanmagan bo'lsa fallback: asosiy do'kon (type='b') avtomatik aniqlanadi."""
        base_stores = list(Store.objects.filter(is_active=True, type=Store.StoreType.BASE)[:2])
        if len(base_stores) == 0:
            return None, "Asosiy do'kon (type='b') topilmadi."
        if len(base_stores) > 1:
            return None, "Bir nechta faol asosiy do'kon mavjud — sozlamalarda bittasini qoldiring."
        return base_stores[0], None


@extend_schema(
    tags=["Stock Entry"],
    summary="Excel importni tahlil qilish — bazada yo'q mahsulotlarni aniqlash",
    request={
        "multipart/form-data": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "format": "binary", "description": "Kirim Excel fayli (.xlsx)"},
            },
            "required": ["file"],
        }
    },
    responses={200: OpenApiTypes.OBJECT},
)
class StockEntryImportAnalyzeAPIView(APIView):
    """
    Faylni import qilmasdan tahlil qiladi: qancha satr mavjud mahsulotga mos
    kelishi, qaysi satrlar yangi mahsulot ekani (new_products) va qaysi satrlar
    xato sabab o'tkazib yuborilishi (skipped) qaytariladi. DB ga yozmaydi.
    """
    permission_classes = [IsSuperUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file, file_error = _extract_xlsx(request)
        if file_error:
            return file_error

        if file.size > StockEntryImportService.MAX_FILE_SIZE:
            return Response(
                {"detail": f"Fayl hajmi {StockEntryImportService.MAX_FILE_SIZE // (1024*1024)}MB dan oshmasligi kerak."},
                status=400,
            )

        try:
            result = StockEntryImportService.analyze_from_excel(
                file=file,
                max_rows=StockEntryImportService.MAX_SYNC_ROWS,
            )
        except ValidationError as e:
            return Response({"detail": e.messages[0] if hasattr(e, "messages") else str(e)}, status=400)

        return Response(result, status=200)


@extend_schema(
    tags=["Stock Entry"],
    summary="Kirim import shablonini yuklab olish",
)
class StockEntryImportTemplateAPIView(APIView):
    permission_classes = [IsSuperUser]

    def get(self, request):
        if not os.path.exists(TEMPLATE_PATH):
            return Response({"detail": "Shablon fayl topilmadi."}, status=404)

        # YAXSHI: Shablon FileResponse orqali stream qilinadi (butun fayl xotiraga bir martaga yuklanmaydi),
        # DB ga ham murojaat yoq - bu GET tez va xavfsiz.
        return FileResponse(
            open(TEMPLATE_PATH, "rb"),
            as_attachment=True,
            filename="xarid_shablon.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# POST (import): cheklovsiz katta Excel (~48k qator) bitta requestda xotiraga yuklanadi va bitta
#   tranzaksiyada INSERT qilinadi - RAM/timeout/lock xavfi (asosiy sabab stock_entry_import_service.py da).
# GET (template): FileResponse bilan streamlanadi, DB murojaati yoq - namunali yaxshi yechim.
# Kritik muammolar soni: 1 (limitsiz massiv import)
# Performance muammolari: 1 (_resolve_base_store 2 query)
# Arxitektura muammolari: 1 (massiv import sinxron requestda - background task kerak)
# Umumiy baho: 6 / 10
# Prioritet boyicha birinchi hal qilinishi kerak: [import uchun satr/hajm limiti + background task + bulk_create]
# ═══════════════════════════════