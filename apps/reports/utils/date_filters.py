# utils/date_filters.py

from datetime import timedelta
from django.utils import timezone


class DateRangeResolver:

    @staticmethod
    def resolve(filter_type: str, from_date=None, to_date=None):
        now = timezone.now()

        if from_date and to_date:
            return from_date, to_date

        if filter_type == "daily":
            return now.replace(hour=0, minute=0, second=0), now

        if filter_type == "weekly":
            start = now - timedelta(days=7)
            return start, now

        if filter_type == "monthly":
            start = now - timedelta(days=30)
            return start, now

        if filter_type == "yearly":
            start = now - timedelta(days=365)
            return start, now

        return None, None