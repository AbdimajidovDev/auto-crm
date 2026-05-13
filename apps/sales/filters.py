from django_filters import rest_framework as django_filters
from django.db.models import Q
from django_filters import rest_framework as filters
from apps.sales.models import Sale
from apps.store.models import Store
from django.contrib.auth import get_user_model

from apps.users.models.customers import Customer


# ─────────────────────────────────────────────
# FILTER
# ─────────────────────────────────────────────
from django.contrib.auth import get_user_model
from django.db.models import Q
from django_filters import rest_framework as filters

from apps.sales.models import Sale

User = get_user_model()


class SaleFilter(filters.FilterSet):
    """
    Muammo:
      fields = {"store": ["exact"]} — django-filters FK uchun
      avtomatik ModelChoiceFilter yaratadi, lekin Sale modelida
      store = ForeignKey('store.Store', ...) string bilan yozilgan.
      Django lazy resolution qiladi, django-filters esa darhol
      _default_manager ga murojaat qiladi → AttributeError.

    Yechim:
      Har bir FK filter uchun NumberFilter aniq yozildi.
      NumberFilter faqat int qiymat kutadi — model resolve shart emas.
    """

    # ✅ YAXSHI: FK filterlar `NumberFilter` bilan yozilgan, filter form ochilganda Store/User/Customer `.all()` yuklanmaydi.
    # FK filterlar — NumberFilter bilan (string FK muammosi yo'q)
    store    = filters.NumberFilter(field_name="store__id")
    customer = filters.NumberFilter(field_name="customer__id")
    seller   = filters.NumberFilter(field_name="seller__id")

    # Status — oddiy CharField, dict yozuvi xavfsiz
    status = filters.CharFilter(field_name="status", lookup_expr="exact")

    # Sana oralig'i
    date_from = filters.DateFilter(field_name="created_at__date", lookup_expr="gte")
    date_to   = filters.DateFilter(field_name="created_at__date", lookup_expr="lte")

    class Meta:
        model  = Sale
        fields = ["status", "store", "customer", "seller", "date_from", "date_to"]


#
# User = get_user_model()
#
#


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [created_at/store/customer/seller indekslari mavjudligini DB migratsiyalarda tekshirish]
# ═══════════════════════════════
# class SaleFilter(django_filters.FilterSet):
#     # PERFORMANCE: filter forma ochilganda butun `Store`/`User`/`Customer` jadvallari yuklanadi —
#     # katta bazada sekin ochilish; `only("id", "name")` yoki AJAX qidiruv ixtiyoriy yaxshilanish.
#     store = django_filters.ModelChoiceFilter(queryset=Store.objects.all())
#     seller = django_filters.ModelChoiceFilter(queryset=User.objects.all())
#     customer = django_filters.ModelChoiceFilter(queryset=Customer.objects.all())
#
#     date_from = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
#     date_to = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")
#
#     search = django_filters.CharFilter(method="filter_search")
#
#     class Meta:
#         model = Sale
#         fields = ["status", "store", "seller", "customer"]
#
#     def filter_search(self, queryset, name, value):
#         return queryset.filter(
#             Q(customer__first_name__icontains=value) |
#             Q(customer__last_name__icontains=value) |
#             Q(customer__phone_number__icontains=value)
#         )
#
#
