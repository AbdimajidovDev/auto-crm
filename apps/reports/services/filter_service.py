# apps/reports/services/filter_service.py

from django.core.exceptions import ValidationError
from apps.store.models import Store


class ReportFilterService:

    @staticmethod
    def resolve_store(branch_id):
        if not branch_id or branch_id == "all":
            return None

        try:
            branch_id = int(branch_id)
        except ValueError:
            raise ValidationError("branchId noto‘g‘ri")

        if not Store.objects.filter(id=branch_id).exists():
            raise ValidationError("Store mavjud emas")

        return [branch_id]