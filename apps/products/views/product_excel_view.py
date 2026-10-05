"""
views.py — ProductImportAPIView
"""
import os
from django.core.exceptions import ValidationError
from django.http import FileResponse
from drf_spectacular.utils import extend_schema, OpenApiTypes
from rest_framework import permissions
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.store_scope import ensure_store_access
from apps.products.services.product_import_service import ProductImportService
from apps.products.services.product_excel_lookup_service import ProductExcelLookupService

from django.conf import settings

TEMPLATE_PATH = os.path.join(
    settings.BASE_DIR,
    "core",
    "templates",
    "mahsulot_shablon.xlsx",
)


def _async_product_import_worker(job):
    with job.input_file.open("rb") as f:
        return ProductImportService.import_from_excel(f, max_rows=None)


@extend_schema(
    tags=["Product"],
    summary="Mahsulotlarni Excel orqali import qilish",
    request={
        "multipart/form-data": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "async": {"type": "boolean", "description": "Fon rejimida qayta ishlash"},
            },
        }
    },
    responses={200: OpenApiTypes.OBJECT, 202: OpenApiTypes.OBJECT},
)
class ProductImportAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file maydoni majburiy."}, status=400)

        if not file.name.endswith(".xlsx"):
            return Response({"detail": "Faqat .xlsx fayl qabul qilinadi."}, status=400)

        if file.size > ProductImportService.MAX_FILE_SIZE:
            return Response(
                {"detail": f"Fayl hajmi {ProductImportService.MAX_FILE_SIZE // (1024*1024)}MB dan oshmasligi kerak."},
                status=400,
            )

        is_async = (
            request.query_params.get("async") in ("true", "1")
            or request.headers.get("X-Async") in ("true", "1")
            or str(request.data.get("async", "")).lower() in ("true", "1")
        )

        if is_async:
            from apps.common.models import AsyncJob
            from apps.common.services.async_job_service import AsyncJobService
            job = AsyncJobService.create_and_submit(
                job_type=AsyncJob.JobType.PRODUCT_IMPORT,
                user=request.user,
                payload={"filename": file.name},
                input_file=file,
                task_func=_async_product_import_worker,
            )
            return Response(
                {
                    "job_id": str(job.id),
                    "status": job.status,
                    "message": "Mahsulotlar fayli fon rejimida qayta ishlanmoqda. Holatni /api/jobs/<job_id>/status/ orqali kuzatib boring.",
                    "status_url": f"/api/jobs/{job.id}/status/",
                },
                status=202,
            )

        try:
            result = ProductImportService.import_from_excel(file, max_rows=ProductImportService.MAX_SYNC_ROWS)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(result, status=200)


@extend_schema(
    tags=["Product"],
    summary="Excel shablondan mahsulotlarni topish (sotuv cheki uchun)",
    description=(
        "Faylni O'QIYDI, hech narsa yozmaydi. Har qator SKU (artikul) → barcode → nom "
        "tartibida qidiriladi; topilganlar tanlangan do'kondagi qoldiq va narxlari "
        "bilan qaytariladi. Miqdor ustuni bo'lsa (masalan kirim shablonidagi "
        "'Miqdori'), miqdor ham fayldan olinadi."
    ),
    request={
        "multipart/form-data": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "format": "binary"},
                "store": {"type": "integer"},
            },
        }
    },
    responses={200: OpenApiTypes.OBJECT},
)
class ProductExcelLookupAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "file maydoni majburiy."}, status=400)

        if not file.name.lower().endswith(".xlsx"):
            return Response({"detail": "Faqat .xlsx fayl qabul qilinadi."}, status=400)

        raw_store = request.data.get("store")
        try:
            store_id = int(raw_store)
        except (TypeError, ValueError):
            return Response({"detail": "store maydoni majburiy."}, status=400)

        # Xodim faqat o'z do'koni bo'yicha qoldiq/narx ko'ra oladi
        ensure_store_access(request.user, store_id)

        try:
            result = ProductExcelLookupService.resolve(file, store_id)
        except ValidationError as e:
            return Response({"detail": e.messages[0] if e.messages else str(e)}, status=400)

        return Response(result, status=200)


@extend_schema(
    tags=["Product"],
    summary="Mahsulot import shablonini yuklab olish",
)
class ProductImportTemplateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not os.path.exists(TEMPLATE_PATH):
            return Response({"detail": "Shablon fayl topilmadi."}, status=404)

        # ✅ YAXSHI: GET faqat statik shablon faylni `FileResponse` (stream) orqali qaytaradi —
        # DB so'rovi yo'q, xotiraga to'liq yuklamaydi. Bu GET tomonida perf muammosi yo'q.
        return FileResponse(
            open(TEMPLATE_PATH, "rb"),
            as_attachment=True,
            filename="mahsulot_shablon.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0  (bu view'ning o'zida — og'irlik POST import servisida, product_import_service.py ga qarang)
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Izoh: GET (shablon yuklab olish) stream bilan — yaxshi. Import og'irligi ProductImportService ichida (alohida auditlangan).
# Prioritet bo'yicha birinchi hal qilinishi kerak: [product_import_service.py dagi per-row save() sikli — bulk_create]
# ═══════════════════════════════
