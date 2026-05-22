# # apps/reports/services/filter_service.py
# from rest_framework.exceptions import ValidationError
#
# from apps.store.models import Store
#
#
# class ReportFilterService:
#
#     @staticmethod
#     def resolve_store(store_id):
#         if not store_id or store_id == "all":
#             return None
#
#         try:
#             store_id = int(store_id)
#         except ValueError:
#             raise ValidationError("branchId noto‘g‘ri")
#
#         if not Store.objects.filter(id=store_id).exists():
#             raise ValidationError("Store mavjud emas")
#
#         return [store_id]