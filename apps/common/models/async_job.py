import uuid
from django.conf import settings
from django.db import models


class AsyncJob(models.Model):
    class JobType(models.TextChoices):
        STOCK_ENTRY_IMPORT = "stock_entry_import", "Kirim importi"
        PRODUCT_IMPORT = "product_import", "Mahsulotlar importi"
        EXCEL_EXPORT = "excel_export", "Excel eksport"

    class JobStatus(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        PROCESSING = "processing", "Bajarilmoqda"
        COMPLETED = "completed", "Muvaffaqiyatli yakunlandi"
        FAILED = "failed", "Xatolik bilan tugadi"
        CANCELLED = "cancelled", "Bekor qilindi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=64, choices=JobType.choices, db_index=True)
    status = models.CharField(
        max_length=32, choices=JobStatus.choices, default=JobStatus.PENDING, db_index=True
    )
    progress = models.PositiveSmallIntegerField(default=0)
    store = models.ForeignKey(
        "store.Store",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="async_jobs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="async_jobs",
    )
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    input_file = models.FileField(upload_to="async_jobs/inputs/%Y/%m/", null=True, blank=True)
    output_file = models.FileField(upload_to="async_jobs/outputs/%Y/%m/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["store", "job_type"]),
        ]

    def __str__(self):
        return f"AsyncJob({self.id}, {self.job_type}, {self.status})"
