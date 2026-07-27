"""
Mahsulotlar ustida ommaviy (bulk) amallar:

  POST /api/products/bulk-status/  {"ids": [1,2], "action": "archive"|"unarchive"}
      — tanlangan mahsulotlarni arxivlash (status='i') yoki arxivdan
        chiqarish (status='a'). RBAC: products.archive huquqi kerak.

  POST /api/products/bulk-delete/  {"ids": [1,2]}
      — FAQAT arxivlangan mahsulotlarni butunlay o'chirish. Faqat superadmin.
        Kirim/sotuv tarixida ishlatilgan mahsulot ProtectedError beradi —
        bunday yozuvlar o'chirilmasdan sabab bilan qaytariladi.
"""

from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product
from apps.users.permissions import user_has_perm


def _parse_ids(data):
    """Body'dan ids ro'yxatini oladi; noto'g'ri format → None."""
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        return None
    try:
        return [int(i) for i in ids]
    except (TypeError, ValueError):
        return None


@extend_schema(
    tags=["Product"],
    summary="Mahsulotlarni ommaviy arxivlash / arxivdan chiqarish "
            "(body: {ids: [..], action: 'archive'|'unarchive'})",
)
class ProductBulkStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        ids = _parse_ids(request.data)
        if ids is None:
            return Response({"detail": "ids — butun sonlar ro'yxati bo'lishi kerak."}, status=400)

        action = request.data.get("action")
        if action not in ("archive", "unarchive"):
            return Response({"detail": "action 'archive' yoki 'unarchive' bo'lishi kerak."}, status=400)

        if not user_has_perm(request.user, "products.archive"):
            return Response(
                {"detail": "Ruxsat yo'q: mahsulotni arxivlash uchun 'products.archive' huquqi kerak."},
                status=403,
            )

        new_status = (
            Product.ProductStatus.INACTIVE
            if action == "archive"
            else Product.ProductStatus.ACTIVE
        )

        # .update() auto_now ni ishlatmaydi — updated_at qo'lda beriladi
        updated = (
            Product.objects
            .filter(id__in=ids)
            .exclude(status=new_status)
            .update(status=new_status, updated_at=timezone.now())
        )

        return Response({"updated": updated}, status=200)


@extend_schema(
    tags=["Product"],
    summary="Arxivlangan mahsulotlarni butunlay o'chirish (faqat superadmin; "
            "body: {ids: [..]}). Kirim/sotuvda ishlatilganlari o'chirilmaydi — "
            "failed ro'yxatida sabab bilan qaytadi",
)
class ProductBulkDeleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": "Ruxsat yo'q: mahsulotni butunlay o'chirish faqat superadmin uchun."},
                status=403,
            )

        ids = _parse_ids(request.data)
        if ids is None:
            return Response({"detail": "ids — butun sonlar ro'yxati bo'lishi kerak."}, status=400)

        products = list(Product.objects.filter(id__in=ids))
        found_ids = {p.id for p in products}

        deleted = []
        failed = []
        # Faqat arxivlangani o'chiriladi — faol mahsulot avval arxivlanishi shart
        for product in products:
            if product.status != Product.ProductStatus.INACTIVE:
                failed.append({
                    "id": product.id,
                    "name": product.name,
                    "reason": "Mahsulot arxivlanmagan — avval arxivlang.",
                })
                continue
            try:
                # Har biri alohida tranzaksiyada: bittasi ProtectedError bersa,
                # qolganlarining o'chishi bekor bo'lmasin.
                # delete() dan keyin product.pk None bo'ladi — id oldindan olinadi
                product_id = product.id
                with transaction.atomic():
                    product.delete()
                deleted.append(product_id)
            except ProtectedError:
                failed.append({
                    "id": product.id,
                    "name": product.name,
                    "reason": "Kirim/sotuv tarixida ishlatilgan — o'chirib bo'lmaydi.",
                })

        for missing_id in set(ids) - found_ids:
            failed.append({
                "id": missing_id,
                "name": "",
                "reason": "Mahsulot topilmadi.",
            })

        return Response({"deleted": deleted, "failed": failed}, status=200)
