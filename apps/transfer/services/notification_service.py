from django.db import transaction
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.transfer.models import Notification
from apps.store.models import StoreUser


class NotificationService:

    @staticmethod
    def _send_notifications(*, user_ids, notif_type, title, message, transfer):

        def _send():
            channel_layer = get_channel_layer()

            # 🔥 create notifications
            notifications = [
                Notification(
                    user_id=user_id,
                    type=notif_type,
                    title=title,
                    message=message,
                    transfer=transfer
                )
                for user_id in user_ids
            ]

            # 🔥 bulk insert + ids qaytadi (PostgreSQL)
            # ✅ YAXSHI: Notification yozuvlari `bulk_create` bilan yaratilgan, loop ichida alohida INSERT qilinmayapti.
            notifications = Notification.objects.bulk_create(notifications)

            # 🔥 websocket fanout
            # ⚠️ MUAMMO [PERFORMANCE]: WebSocket fanout sinxron loop ichida ketma-ket yuboriladi.
            # Sabab: har bir notification uchun `async_to_sync(group_send)` alohida chaqiriladi.
            # Natija: userlar soni ko'p bo'lsa commitdan keyingi callback uzoq ishlaydi va worker band bo'ladi.
            # ✅ YECHIM:
            # celery_task_send_notifications.delay([notif.id for notif in notifications])
            for notif in notifications:
                async_to_sync(channel_layer.group_send)(
                    f"user_{notif.user_id}",
                    {
                        "type": "notify",
                        "data": {
                            "id": notif.id,
                            "title": notif.title,
                            "message": notif.message,
                            "type": notif.type,
                            "transfer_id": transfer.id
                        }
                    }
                )

        # ❗ faqat commitdan keyin
        transaction.on_commit(_send)

    # =========================
    # EVENTS
    # =========================

    @staticmethod
    def notify_transfer_created(transfer):

        # ✅ YAXSHI: `values_list("user_id", flat=True)` faqat kerakli ustunni oladi.
        user_ids = set(
            StoreUser.objects.filter(
                store=transfer.to_store,
                is_active=True
            ).values_list("user_id", flat=True)
        )

        NotificationService._send_notifications(
            user_ids=user_ids,
            notif_type=Notification.Type.TRANSFER_CREATED,
            title="Yangi transfer",
            message=f"{transfer.from_store.name} dan yangi transfer keldi",
            transfer=transfer
        )

    @staticmethod
    def notify_transfer_rejected(transfer):

        user_ids = set(
            StoreUser.objects.filter(
                store=transfer.from_store,
                is_active=True
            ).values_list("user_id", flat=True)
        )

        # initiatorni qo‘shamiz
        if transfer.created_by_id:
            user_ids.add(transfer.created_by_id)

        NotificationService._send_notifications(
            user_ids=user_ids,
            notif_type=Notification.Type.TRANSFER_REJECTED,
            title="Transfer rad etildi",
            message=f"{transfer.to_store.name} transferni rad etdi",
            transfer=transfer
        )


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [WebSocket fanoutni background taskga chiqarish]
# ═══════════════════════════════
