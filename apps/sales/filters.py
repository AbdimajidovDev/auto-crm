from django_filters import rest_framework as django_filters
from django.db.models import Q

from apps.sales.models import Sale
from apps.store.models import Store
from django.contrib.auth import get_user_model

from apps.users.models.customers import Customer

User = get_user_model()


class SaleFilter(django_filters.FilterSet):
    store = django_filters.ModelChoiceFilter(queryset=Store.objects.all())
    seller = django_filters.ModelChoiceFilter(queryset=User.objects.all())
    customer = django_filters.ModelChoiceFilter(queryset=Customer.objects.all())

    date_from = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    date_to = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="lte")

    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Sale
        fields = ["status", "store", "seller", "customer"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(customer__first_name__icontains=value) |
            Q(customer__last_name__icontains=value) |
            Q(customer__phone_number__icontains=value)
        )