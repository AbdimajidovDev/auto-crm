# users/views.py
from drf_spectacular.utils import extend_schema
from rest_framework.generics import ListAPIView, get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from apps.common.paginations import StandardPagination
from apps.store.models import StoreUser
from apps.users.models import User
from apps.users.services import UserService
from apps.users.serializers import SellerCreateSerializer, UserResponseSerializer
from apps.users.selectors import UserSelector
from apps.users.serializers import UserSerializer


@extend_schema(
    tags=['User'],
    summary="- Seller yaratish uchun API",
)
class UsersListView(ListAPIView):
    # ⚠️ MUAMMO [ARXITEKTURA]: Class darajasidagi `queryset` `get_queryset()` bilan qayta yozilgan (dead code).
    # Sabab: `get_queryset()` mavjud bo'lsa DRF class-level `queryset`ni ishlatmaydi, faqat router basename uchun ushlab qoladi.
    # Natija: import vaqtida `UserSelector.list_users()` chaqiriladi (lazy bo'lsa ham) va o'quvchini chalg'itadi.
    # ✅ YECHIM: ikkita manbadan birini qoldiring — yoki class-level `queryset`, yoki `get_queryset()`.
    queryset = UserSelector.list_users()
    serializer_class = UserSerializer
    # Shaxsiy ma'lumotlar (ism/telefon/email) — faqat autentifikatsiyalangan
    # userlar ko'ra oladi; rol darajasidagi cheklov RBAC middleware'da (users.view).
    permission_classes = (permissions.IsAuthenticated,)
    # ✅ BAJARILDI — pagination qo'shildi (TransferListAPIView bilan bir xil yechim):
    #   AVVAL: pagination_class = None → ro'yxat paginationsiz, butun jadval bir javobda + har qatorga
    #          store prefetch yuklanardi (user/seller soni o'sgani sari sekinlashardi).
    #   HOZIR: StandardPagination — har sahifada 20 qator (`?page=` / `?limit=`). Prefetch avvaldan bor edi,
    #          shu sabab N+1 yo'q. ListAPIView pagination'ni avtomatik qo'llaydi — logika o'zgarmadi.
    pagination_class = StandardPagination

    def get_queryset(self):
        from django.db.models import Prefetch

        # ✅ YAXSHI: `store_links` `is_active` filtri + `select_related("store")` bilan `to_attr` ga prefetch qilingan.
        # Bu `UserSerializer.get_store_id/get_store_name` (active_store_links[0].store) uchun N+1 ni yo'q qiladi.
        # ⚠️ MUAMMO [PERF]: `.only(...)` yo'q — User modelining barcha ustunlari (parol hashi ham) SELECT qilinadi.
        # ✅ YECHIM: serializer faqat 8 ta maydondan foydalanadi, shu bois:
        #   .only("id", "full_name", "phone_number", "email", "is_active", "created_at", "updated_at")
        # order_by shart: tartibsiz queryset + pagination barqaror emas va
        # yangi user oxirgi sahifaga tushib, ro'yxatda "ko'rinmay" qolardi
        queryset = User.objects.select_related("role").prefetch_related(
            Prefetch(
                "store_links",
                queryset=StoreUser.objects.filter(is_active=True).select_related("store"),
                to_attr="active_store_links"
            )
        ).filter(is_superuser=False).order_by("-id")
        return queryset




@extend_schema(
    tags=['User'],
    summary="- ID orqali bitta Userni ko'rish uchun API.",
)
class UsersDetailView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = UserSerializer

    def get(self, request, pk):
        # ⚠️ MUAMMO [PERF/ARXITEKTURA]: `UserSerializer` `get_store_id/get_store_name` uchun `active_store_links`
        # (to_attr) atributiga tayanadi, lekin bu yerda `store_links` prefetch qilinmagan.
        # Natija: serializerdagi `getattr(obj, "active_store_links", [])` doim bo'sh → store_id/name doim None,
        # yoki prefetch qo'shilsa har chaqiruvda StoreUser bo'yicha qo'shimcha so'rov (N+1) bo'ladi.
        # ✅ YECHIM: list view bilan bir xil prefetch ishlatish:
        #   qs = User.objects.prefetch_related(
        #       Prefetch("store_links",
        #                queryset=StoreUser.objects.filter(is_active=True).select_related("store"),
        #                to_attr="active_store_links"))
        #   user = get_object_or_404(qs, pk=pk)
        user = get_object_or_404(User.objects.select_related("role"), pk=pk)
        serializer = self.serializer_class(user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        user = UserSelector.get_user_by_id(user_id=pk)
        serializer = self.serializer_class(user, data=request.data)
        if serializer.is_valid():
            serializer.save()
            # UserSerializer'da store_id read-only (SerializerMethodField) —
            # do'kon bog'lamasi alohida sync qilinadi, kalit kelgandagina.
            if "store_id" in request.data:
                UserService.set_user_store(user=user, store_id=request.data.get("store_id"))
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        user = UserSelector.get_user_by_id(user_id=pk)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



@extend_schema(
    tags=['User'],
    summary="- Seller yaratish uchun API",
)
class SellerCreateAPIView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = SellerCreateSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user = UserService.create_seller_with_store(
                request_user=request.user,
                data=serializer.validated_data
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)

        return Response(
            UserResponseSerializer(user).data,
            status=status.HTTP_201_CREATED
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI (yangilangan)
# Kritik muammolar soni: 0
# Performance muammolari: 2  (avval 3 — pagination TUZATILDI; qolgani: list .only() yo'q, detailda prefetchsiz store)
# Arxitektura muammolari: 3  (class-level queryset dead code; AllowAny ochiq ro'yxat; detail/list prefetch mos emas)
# Umumiy baho: 7 / 10  (avval 6/10)
# ✅ BAJARILDI: UsersListView → pagination_class = StandardPagination (paginationsiz to'liq jadval muammosi hal qilindi).
# Prioritet bo'yicha birinchi hal qilinishi kerak:
#   [1] AllowAny -> IsAuthenticated (shaxsiy ma'lumot himoyasi) — LOGIKA/xavfsizlik qarori, tegilmadi
#   [2] UsersDetailView'ga list bilan bir xil prefetch qo'shish
# ═══════════════════════════════
