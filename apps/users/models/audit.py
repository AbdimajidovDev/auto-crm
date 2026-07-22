from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """
    Amallar jurnali — tizimdagi har bir muhim amal (yaratish/tahrirlash/
    o'chirish/arxivlash, login/logout) kim, qachon, qayerdan (IP) va qaysi
    qurilmadan qilinganini saqlaydi.

    Yozuvlar core/middleware/audit.py (avtomatik) va login/logout view'lari
    orqali tushadi. Har bir yozuv o'z sanasidan AUDIT_LOG_RETENTION_DAYS
    (standart 60) kun o'tgach avto o'chiriladi — prune_expired() ni
    middleware kuniga bir marta chaqiradi.
    """

    class Action(models.TextChoices):
        CREATE = "create", "Qo'shish"
        EDIT = "edit", "Tahrirlash"
        DELETE = "delete", "O'chirish"
        ARCHIVE = "archive", "Arxivlash"
        LOGIN = "login", "Kirish"
        LOGOUT = "logout", "Chiqish"

    user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    # User o'chirilsa ham jurnalda kim ekani ko'rinib turishi uchun snapshot
    user_display = models.CharField(max_length=160, blank=True, default="")

    module = models.CharField(max_length=32, db_index=True)
    action = models.CharField(max_length=16, choices=Action.choices, db_index=True)

    method = models.CharField(max_length=8, blank=True, default="")
    path = models.CharField(max_length=255, blank=True, default="")
    object_id = models.CharField(max_length=32, blank=True, default="")
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)

    # Sanitizatsiya qilingan so'rov tanasi (parollarsiz, hajmi cheklangan)
    details = models.JSONField(null=True, blank=True)

    ip_address = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"{self.user_display or 'Anonim'} — {self.module}.{self.action} ({self.created_at:%Y-%m-%d %H:%M})"

    @classmethod
    def retention_cutoff(cls):
        days = getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 60)
        return timezone.now() - timedelta(days=days)

    @classmethod
    def prune_expired(cls) -> int:
        """Muddati (60 kun) o'tgan yozuvlarni o'chiradi, sonini qaytaradi."""
        deleted, _ = cls.objects.filter(created_at__lt=cls.retention_cutoff()).delete()
        return deleted

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["module", "action"]),
        ]
