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

from apps.products.services.product_import_service import ProductImportService


TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),   # bu fayl joylashgan papka
    "templates",
    "mahsulot_shablon.xlsx",
)


@extend_schema(
    tags=["Product"],
    summary="Mahsulotlarni Excel orqali import qilish",
    request={"multipart/form-data": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}},
    responses={200: OpenApiTypes.OBJECT},
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

        try:
            result = ProductImportService.import_from_excel(file)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

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

        return FileResponse(
            open(TEMPLATE_PATH, "rb"),
            as_attachment=True,
            filename="mahsulot_shablon.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
