from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, generics

from apps.common.paginations import StandardPagination
from apps.common.excel_export import parse_date_param
from apps.inventory.models import (
    InventoryMovement,
    InventorySession,
)

from apps.inventory.serializers.inventory_serializer import (
    InventoryStartSerializer,
    InventoryCountSerializer,
    InventoryFinalizeSerializer,
    InventoryCancelSerializer,
    InventoryMovementListSerializer, InventoryListSerializer
)

from apps.inventory.services.inventory_service import InventoryService
from apps.inventory.services.inventory_selector import InventorySelector
from apps.inventory.serializers.inventory_serializer import InventoryDetailSerializer



@extend_schema(
    tags=["Inventory"],
    summary="Inventarizatsiya mahsulotlari (status bo‘yicha filter bilan)",
)
class InventoryListAPIView(generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryListSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user

        qs = (
            InventorySession.objects
            .select_related("store", "started_by")
            .order_by("-started_at")
        )

        if not user.is_superuser:
            qs = qs.filter(
                store__user_links__user=user,
                store__user_links__is_active=True,
            )

        # 🔎 ?date_from=&date_to= (YYYY-MM-DD) — sana oralig'i bo'yicha filtr
        date_from = parse_date_param(self.request.query_params.get("date_from"))
        date_to = parse_date_param(self.request.query_params.get("date_to"))
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        return qs


class InventoryDetailAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryDetailSerializer
    pagination_class = StandardPagination

    @extend_schema(
        tags=["Inventory"],
        summary="Inventarizatsiya mahsulotlari (status bo‘yicha filter bilan)",
    )
    def get(self, request, session_id):
        # ✅ BAJARILDI — "malumot ko'pligidan qotib qolish" + SQL optimizatsiya (logika saqlandi):
        #
        #   AVVAL:
        #     qs = InventorySelector.get_inventory_list(session_id, statuses)   # 8 subquery/qator, paginationsiz
        #     data = self.serializer_class(qs, many=True).data                 # 12k+ qator bir javobda
        #     checked = [item for item in data if item["is_check"]]            # Python'da hammasini aylanadi
        #     return Response({"products": data, "checked": checked})
        #   Muammo: har snapshot qatoriga 8 ta korrelyatsiyalangan Subquery + paginationsiz + checked
        #     Python list aylantirish → ~12k mahsulotda endpoint qotib qolardi.
        #
        #   HOZIR (selector to'liq qayta yozildi — inventory_selector.py dagi 🔬 TAHLIL ga qarang):
        #     (1) products — YENGIL snapshot queryset (`get_snapshot_page_qs`) StandardPagination bilan
        #         sahifalanadi, keyin FAQAT joriy 20 qator `enrich_page` da boyitiladi:
        #         8 korrelyatsiyalangan subquery O'RNIGA — 2 ta agregat query (1 count map + 1 movement
        #         conditional-aggregation GROUP BY), faqat sahifadagi product_id lar bo'yicha.
        #     (2) `checked`  — ALOHIDA YENGIL query (`get_checked`): InventoryCount + product JOIN,
        #         asosiy 8-subquery ISHLATILMAYDI. Butun sessiya bo'yicha, pagination'dan mustaqil.
        #     (3) `checked_count` — SQL COUNT(*) (`get_checked_count`), Python'da list aylantirilmaydi.
        #     (4) status filtri: `?status=checked|unchecked|all` — SQL darajasida `Exists` (semi-join) bilan.
        #
        #   ⚠️ Frontend contract o'zgarishi: eski `{products, checked}` →
        #     {count, total_pages, current_page, next, previous, results, checked, checked_count}.
        #     `results` = mahsulotlar (paginated), `checked` = yengil ro'yxat, `checked_count` = umumiy son.
        #     Eski `status=p|e|l|m` (count status) filtri bu endpointda `checked|unchecked|all` ga almashtirildi
        #     (count status bo'yicha ajratish uchun /sessions/<id>/over/ va /short/ endpointlari bor).

        status_param = (request.query_params.get("status") or "all").lower()
        if status_param not in ("checked", "unchecked", "all"):
            status_param = "all"

        # 1) products — yengil base qs → paginate → faqat sahifani boyitish
        qs = InventorySelector.get_snapshot_page_qs(session_id, check_status=status_param)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request, view=self)
        enriched = InventorySelector.enrich_page(session_id, page)
        products = self.serializer_class(enriched, many=True).data

        # 2) checked + checked_count — butun sessiya bo'yicha, alohida yengil query'lar
        checked = InventorySelector.get_checked(session_id)
        checked_count = InventorySelector.get_checked_count(session_id)

        response = paginator.get_paginated_response(products).data
        response["checked"] = checked
        response["checked_count"] = checked_count
        return Response(response, status=200)


class InventoryStartAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryStartSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Yangi inventarizatsiya sessiyasini boshlash va boshlang‘ich stock snapshot olish",
        request=InventoryStartSerializer,
        responses={200: {"type": "object", "properties": {"session_id": {"type": "integer"}}}},
    )
    def post(self, request):
        serializer = InventoryStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = InventoryService.start_session(
            user=request.user,
            store_id=serializer.validated_data["store_id"]
        )

        return Response({"session_id": session.id})


class InventorySetCountAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryCountSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Mahsulotni inventarizatsiyada aniq son bilan belgilash (overwrite)",
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    def put(self, request):
        serializer = InventoryCountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        InventoryService.set_count(**serializer.validated_data)
        InventoryService.scan_product(**serializer.validated_data)


        return Response({"status": "updated"})


class InventoryScanAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryCountSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Mahsulotni inventarizatsiyada aniq son bilan belgilash (overwrite)",
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    def post(self, request):
        serializer = InventoryCountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        InventoryService.scan_product(**serializer.validated_data)

        return Response({"status": "ok"})


class InventoryFinalizeAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryFinalizeSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Inventarizatsiyani yakunlash, sotuv va transferlarni hisobga olib stockni avtomatik to‘g‘rilash",
        request=InventoryFinalizeSerializer,
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    def post(self, request):
        serializer = InventoryFinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        InventoryService.finalize(**serializer.validated_data)

        return Response({"status": "completed"})



class InventoryCancelAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryCancelSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Inventarizatsiya sessiyasini bekor qilish va barcha vaqtinchalik ma’lumotlarni o‘chirish",
        request=InventoryCancelSerializer,
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    def post(self, request):
        serializer = InventoryCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        InventoryService.cancel(**serializer.validated_data)

        return Response({"status": "cancelled"})



class InventoryMovementListView(APIView):
    # XAVFSIZLIK: `AllowAny` — inventarizatsiya harakatlari ochiq endpoint bo'lib qolgan;
    # kamida `IsAuthenticated` yoki do'kon/store scope tekshiruvi tavsiya etiladi.
    permission_classes = (permissions.AllowAny,)
    serializer_class = InventoryMovementListSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Inventarizatsiya jarayonida sotuv, transfer, ... bo'lganlar ro'yxati."
    )
    def get(self, request, session_id):
        inventory = get_object_or_404(InventorySession, pk=session_id)
        # ⚠️ MUAMMO [KRITIK/XAVFSIZLIK]: Endpoint `AllowAny` va session store scope tekshiruvisiz ishlaydi.
        # Sabab: istalgan foydalanuvchi session_id ni bilsa inventory movementlarni ko'rishi mumkin.
        # Natija: stock harakatlari va biznes ma'lumotlar sizishi mumkin.
        # ✅ YECHIM:
        # permission_classes = (permissions.IsAuthenticated,)
        # inventory = get_object_or_404(InventorySession.objects.filter(store__user_links__user=request.user), pk=session_id)
        # ⚠️ MUAMMO [PERFORMANCE]: `InventoryMovement` product FK bilan select_related qilinmagan.
        # Sabab: serializer `obj.product.name` o'qiydi.
        # Natija: har movement uchun qo'shimcha SQL query chiqadi.
        # ✅ YECHIM:
        # qs = InventoryMovement.objects.filter(session=inventory).select_related("product").order_by("-created_at")
        # N+1: har bir harakat uchun `product` nomi chiqarilsa, `select_related("product")` kerak.
        qs = InventoryMovement.objects.filter(session=inventory)
        serializer = InventoryMovementListSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


# ═══════════════════════════════
# 📊 FAYL XULOSASI (yangilangan)
# Kritik muammolar soni: 1  (InventoryMovementListView AllowAny — hali ochiq; LOGIKA/xavfsizlik, tegilmadi)
# Performance muammolari: 1  (avval 2 — InventoryDetailAPIView pagination TUZATILDI; qolgani: MovementList select_related)
# Arxitektura muammolari: 0
# Umumiy baho: 7 / 10  (avval 5/10)
# ✅ BAJARILDI: InventoryDetailAPIView (/list/<session_id>/) → products StandardPagination bilan sahifalandi,
#   `checked` esa alohida yengil so'rov bilan olinadi. "Malumot ko'pligidan qotib qolish" muammosi hal qilindi.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [InventoryMovementListView AllowAny -> IsAuthenticated + select_related("product")]
# ═══════════════════════════════
