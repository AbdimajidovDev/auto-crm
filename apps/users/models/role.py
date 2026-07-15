from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


class Role(TimestampMixin):
    """
    Tizim roli — userga biriktiriladi va uning qaysi modullarga kirishi,
    qanday amallar bajarishi mumkinligini belgilaydi.

    permissions — "<modul>.<amal>" kodlar ro'yxati (JSON),
    katalog: apps/users/permissions.py::PERMISSION_CATALOG.
    """

    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True, default="")
    permissions = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "role"
        ordering = ["name"]
