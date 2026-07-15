from django.db import models


class AuditLog(models.Model):
    """
    Amallar jurnali — tizimdagi har bir muhim amal (yaratish/tahrirlash/
    o'chirish/arxivlash, login/logout) kim, qachon, qayerdan (IP) va qaysi
    qurilmadan qilinganini saqlaydi.

    Yozuvlar core/middleware/audit.py (avtomatik) va login/logout view'lari
    orqali tushadi.
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

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["module", "action"]),
        ]
