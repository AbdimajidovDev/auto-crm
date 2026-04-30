from drf_spectacular.utils import extend_schema
from rest_framework.generics import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

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




class InventoryListAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryListSerializer

    @extend_schema(
        tags=["inventory"],
        summary="Inventarizatsiya mahsulotlari (status bo‘yicha filter bilan)",
    )
    def get(self, request):
        inventories = InventorySession.objects.all()
        serializer = self.serializer_class(inventories, many=True, context={'request': request})
        print('serializer', serializer.data)
        return Response(serializer.data, status=200)


class InventoryDetailAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryDetailSerializer

    @extend_schema(
        tags=["inventory"],
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
        tags=["inventory"],
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
        tags=["inventory"],
        summary="Mahsulotni inventarizatsiyada aniq son bilan belgilash (overwrite)",
        responses={200: {"type": "object", "properties": {"status": {"type": "string"}}}},
    )
    def put(self, request):
        serializer = InventoryCountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        InventoryService.set_count(**serializer.validated_data)

        return Response({"status": "updated"})


class InventoryScanAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InventoryCountSerializer

    @extend_schema(
        tags=["inventory"],
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
        tags=["inventory"],
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
        tags=["inventory"],
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
    permission_classes = (permissions.AllowAny,)
    serializer_class = InventoryMovementListSerializer

    @extend_schema(
        tags=["inventory"],
        summary="Inventarizatsiya jarayonida sotuv, transfer, ... bo'lganlar ro'yxati."
    )
    def get(self, request, session_id):
        inventory = get_object_or_404(InventorySession, pk=session_id)
        qs = InventoryMovement.objects.filter(session=inventory)
        serializer = InventoryMovementListSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
