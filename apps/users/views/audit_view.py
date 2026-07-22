from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import generics, permissions

from apps.common.paginations import StandardPagination
from apps.users.models import AuditLog
from apps.users.serializers.audit_serializer import AuditLogSerializer


@extend_schema(
    tags=["Audit"],
    summary="- Amallar jurnali (filtrlar bilan).",
    parameters=[
        OpenApiParameter("module", OpenApiTypes.STR, description="Modul bo'yicha filter (products, sales, auth, ...)"),
        OpenApiParameter("action", OpenApiTypes.STR, description="Amal bo'yicha filter (create, edit, delete, archive, login, logout)"),
        OpenApiParameter("user_id", OpenApiTypes.INT, description="User ID bo'yicha filter"),
        OpenApiParameter("search", OpenApiTypes.STR, description="User/yo'l/obyekt bo'yicha qidirish"),
        OpenApiParameter("date_from", OpenApiTypes.DATE, description="Sanadan (YYYY-MM-DD)"),
        OpenApiParameter("date_to", OpenApiTypes.DATE, description="Sanagacha (YYYY-MM-DD)"),
    ],
)
class AuditLogListAPIView(generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = AuditLogSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        params = self.request.query_params
        # Saqlash muddati (60 kun) o'tgan yozuvlar avto o'chadi — tozalash hali
        # ishlamagan bo'lsa ham ularni ko'rsatmaymiz
        qs = AuditLog.objects.filter(created_at__gte=AuditLog.retention_cutoff())

        module = params.get("module")
        if module:
            qs = qs.filter(module=module)

        action = params.get("action")
        if action:
            qs = qs.filter(action=action)

        user_id = params.get("user_id")
        if user_id:
            qs = qs.filter(user_id=user_id)

        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(user_display__icontains=search)
                | Q(path__icontains=search)
                | Q(object_id=search)
                | Q(ip_address__icontains=search)
            )

        date_from = params.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        return qs
