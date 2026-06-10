from django.db import models

from apps.common.models.timestamp_mixin import TimestampMixin


class Customer(TimestampMixin):
    full_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=20)

    def __str__(self):
        return self.full_name if self.full_name else 'Mijoz malumoti kiritilmagan'
