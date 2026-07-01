from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.store.serializers import StoreUserAssignSerializer
from apps.store.services import StoreUserService


@extend_schema(
    tags=["Store user"],
    summary="- Do'kon uchun sotuvchi qo'shish.",
)
class StoreUserAttachAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = StoreUserAssignSerializer

    def post(self, request):

        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        store_user = StoreUserService.attach_user(
            request_user=request.user,
            user_id=serializer.validated_data["user_id"],
            store_id=serializer.validated_data["store_id"]
        )

        # ✅ YAXSHI: Javob store_user obyektidan FK id (`user_id`, `store_id`) orqali quriladi —
        # bu related obyektni yuklamaydi (`store_user.user` emas), qo'shimcha query yo'q.
        return Response({
            "id": store_user.id,
            "user_id": store_user.user_id,
            "store_id": store_user.store_id,
            "role": store_user.role
        }, status=status.HTTP_201_CREATED)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Bu faylda GET endpoint yo'q — faqat POST (attach_user). Query performance nuqtai nazaridan
# javob FK id'lari orqali quriladi, N+1 yo'q. Auth/duplicate tekshiruvlari servisda (store_user_service.py).
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [yo'q]
# ═══════════════════════════════