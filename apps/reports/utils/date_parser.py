from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework.exceptions import ValidationError


class DateValidator:

    @staticmethod
    def validate(from_str, to_str):
        if not from_str or not to_str:
            return None, None

        from_dt = parse_datetime(from_str)
        to_dt = parse_datetime(to_str)

        if not from_dt or not to_dt:
            raise ValidationError("Invalid datetime format")

        # 🔥 timezone normalize
        if timezone.is_naive(from_dt):
            from_dt = timezone.make_aware(from_dt)

        if timezone.is_naive(to_dt):
            to_dt = timezone.make_aware(to_dt)

        # 🔴 CORE VALIDATION
        if from_dt > to_dt:
            raise ValidationError("from_date cannot be greater than to_date")

        # 🔥 edge fix (inclusive range)
        to_dt = to_dt.replace(hour=23, minute=59, second=59)

        return from_dt, to_dt
