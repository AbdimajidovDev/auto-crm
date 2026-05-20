from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, generics

from apps.common.paginations import StandardPagination
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

        if user.is_superuser:
            return (
                InventorySession.objects
                .select_related("store", "started_by")
                .order_by("-started_at")
            )

        return (
            InventorySession.objects
            .select_related("store", "started_by")
            .filter(
                store__user_links__user=user,
                store__user_links__is_active=True,
            )
            .order_by("-started_at")
        )


class InventoryDetailAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryDetailSerializer

    @extend_schema(
        tags=["Inventory"],
        summary="Inventarizatsiya mahsulotlari (status bo‘yicha filter bilan)",
    )
    def get(self, request, session_id):

        status_param = request.query_params.get("status")

        statuses = None
        if status_param:
            statuses = [s.strip() for s in status_param.split(",")]

        qs = InventorySelector.get_inventory_list(
            session_id=session_id,
            statuses=statuses
        )

        serializer = self.serializer_class(qs, many=True)
        data = serializer.data

        checked = [item for item in data if item["is_check"]]

        return Response({
            "products": data,
            "checked": checked
        }, status=200)


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
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 2
# Performance muammolari: 2
# Arxitektura muammolari: 0
# Umumiy baho: 5 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [InventoryListAPIView uchun store scope/pagination qo'shish, InventoryMovementListView AllowAny ni yopish]
# ═══════════════════════════════
