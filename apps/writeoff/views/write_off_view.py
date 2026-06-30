from django.db.models import Count
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.paginations import StandardPagination
from apps.writeoff.models import WriteOff
from apps.writeoff.serializers.write_off_serializer import (
    WriteOffCreateSerializer,
    WriteOffDetailSerializer,
    WriteOffListSerializer,
    WriteOffUpdateSerializer,
)
from apps.writeoff.services.write_off_service import WriteOffService


def _assert_store_access(user, store):
    """Superuser hamma do'konga; boshqalar faqat biriktirilgan faol do'koniga."""
    if user.is_superuser:
        return
    if not user.store_links.filter(store=store, is_active=True).exists():
        raise PermissionDenied("Siz ushbu do'kon uchun spisaniye qila olmaysiz.")


def _scoped_queryset(user):
    qs = (
        WriteOff.objects
        .select_related("store", "created_by")
        .annotate(items_count=Count("items"))
    )
    if user.is_superuser:
        return qs
    return qs.filter(
        store__user_links__user=user,
        store__user_links__is_active=True,
    )


@extend_schema(
    tags=["WriteOff"],
    summary="Spisaniye ro'yxati (store-scope, reason filtr, pagination).",
    parameters=[
        OpenApiParameter(name="store", description="Do'kon ID bo'yicha filtr", required=False, type=int),
        OpenApiParameter(name="reason", description="Sabab bo'yicha filtr", required=False, type=str),
        OpenApiParameter(name="limit", description="Sahifadagi elementlar soni", required=False, type=int),
        OpenApiParameter(name="page", description="Sahifa raqami", required=False, type=int),
    ],
)
class WriteOffListAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = WriteOffListSerializer
    pagination_class = StandardPagination

    def get(self, request):
        qs = _scoped_queryset(request.user)

        store = request.query_params.get("store")
        if store:
            qs = qs.filter(store_id=store)

        reason = request.query_params.get("reason")
        if reason:
            qs = qs.filter(reason=reason)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = WriteOffListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


@extend_schema(
    tags=["WriteOff"],
    summary="Spisaniye yaratish — mahsulot qoldiqdan kamayadi.",
    request=WriteOffCreateSerializer,
    responses={201: WriteOffDetailSerializer},
)
class WriteOffCreateAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = WriteOffCreateSerializer

    def post(self, request):
        serializer = WriteOffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        store = data["store"]
        _assert_store_access(request.user, store)

        write_off = WriteOffService.create_write_off(
            store=store,
            items=[{"product": i["product"], "quantity": i["quantity"]} for i in data["items"]],
            reason=data["reason"],
            comment=data.get("comment", ""),
            user=request.user,
        )

        detail = (
            WriteOff.objects
            .select_related("store", "created_by")
            .prefetch_related("items__product")
            .get(pk=write_off.pk)
        )
        return Response(
            WriteOffDetailSerializer(detail).data,
            status=status.HTTP_201_CREATED,
        )


class WriteOffDetailAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = WriteOffDetailSerializer

    def _get_object(self, request, pk):
        write_off = get_object_or_404(
            WriteOff.objects.select_related("store", "created_by").prefetch_related("items__product"),
            pk=pk,
        )
        _assert_store_access(request.user, write_off.store)
        return write_off

    @extend_schema(tags=["WriteOff"], summary="Bitta spisaniye (itemlari bilan).")
    def get(self, request, pk):
        write_off = self._get_object(request, pk)
        return Response(WriteOffDetailSerializer(write_off).data)

    @extend_schema(
        tags=["WriteOff"],
        summary="Spisaniye metama'lumotini tahrirlash (sabab/izoh).",
        request=WriteOffUpdateSerializer,
    )
    def put(self, request, pk):
        write_off = self._get_object(request, pk)

        serializer = WriteOffUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        WriteOffService.update_write_off(
            write_off=write_off,
            reason=serializer.validated_data.get("reason"),
            comment=serializer.validated_data.get("comment"),
        )
        write_off.refresh_from_db()
        return Response(WriteOffDetailSerializer(write_off).data)

    @extend_schema(
        tags=["WriteOff"],
        summary="Spisaniyeni o'chirish — miqdor qoldiqqa qaytariladi.",
    )
    def delete(self, request, pk):
        write_off = self._get_object(request, pk)
        WriteOffService.delete_write_off(write_off=write_off)
        return Response(status=status.HTTP_204_NO_CONTENT)
