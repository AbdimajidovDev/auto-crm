"""
Muddati o'tgan audit log yozuvlarini o'chirish.

Har bir yozuv o'z sanasidan AUDIT_LOG_RETENTION_DAYS (standart 60) kun
o'tgach o'chiriladi. Middleware buni kuniga bir marta avtomatik qiladi;
bu buyruq qo'lda yoki cron/scheduler orqali ishga tushirish uchun:

    python manage.py prune_audit_logs
"""
from django.core.management.base import BaseCommand

from apps.users.models import AuditLog


class Command(BaseCommand):
    help = "Muddati (AUDIT_LOG_RETENTION_DAYS) o'tgan audit log yozuvlarini o'chiradi"

    def handle(self, *args, **options):
        deleted = AuditLog.prune_expired()
        self.stdout.write(self.style.SUCCESS(f"{deleted} ta eski audit log yozuvi o'chirildi"))
