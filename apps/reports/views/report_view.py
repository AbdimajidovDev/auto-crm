from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from apps.reports.services.report_service import ReportService


@extend_schema(
    tags=["Reports"],
    summary="Hisobot — summary, filiallar, kategoriyalar, to'lov tuzilmasi, qarzlar.",
    parameters=[
        OpenApiParameter(
            "filter", OpenApiTypes.STR,
            description="Davr: weekly | monthly | yearly  (default: monthly)",
        ),
        OpenApiParameter(
            "store_id", OpenApiTypes.STR,
            description="Do'kon filtri: 'all' yoki do'kon ID si (default: all)",
        ),
        OpenApiParameter(
            "from", OpenApiTypes.DATE,
            description="Boshlanish sanasi: YYYY-MM-DD (ixtiyoriy)",
        ),
        OpenApiParameter(
            "to", OpenApiTypes.DATE,
            description="Tugash sanasi: YYYY-MM-DD (ixtiyoriy)",
        ),
    ],
)
class ReportsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        try:
            data = ReportService.get(request.query_params)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        return Response(data)
