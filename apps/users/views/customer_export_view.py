from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema

from apps.common.excel_export import BaseExcelExportAPIView
from apps.debts.models import CustomerDebt
from apps.sales.models import Sale
from apps.users.models.customers import Customer


@extend_schema(
    tags=["Customer"],
    summary="Mijozlarni Excel (.xlsx) ga eksport qilish "
            "(?search=&has_debt=&ordering=&date_from=&date_to=)",
)
class CustomerExportAPIView(BaseExcelExportAPIView):
    filename = "mijozlar"
    date_field = "created_at"

    ALLOWED_ORDERINGS = {
        "full_name",
        "-total_purchase_amount",
        "-total_debt",
        "-created_at",
    }

    def get_queryset(self, request):
        # Ro'yxatdagi kabi jami xarid va qarz — lekin sales/items prefetch'siz
        # (eksportga kerak emas, katta hajmda ortiqcha yuk bo'lardi).
        zero = Value(Decimal("0.00"), output_field=DecimalField())

        total_purchase_subquery = (
            Sale.objects.filter(customer=OuterRef("pk"))
            .exclude(status=Sale.Status.RETURNED)
            .values("customer")
            .annotate(total=Sum("total_amount"))
            .values("total")
        )
        debt_in_subquery = (
            CustomerDebt.objects
            .filter(customer=OuterRef("pk"), type=CustomerDebt.Type.INCREASE)
            .values("customer")
            .annotate(total=Sum("amount"))
            .values("total")
        )
        debt_paid_subquery = (
            CustomerDebt.objects
            .filter(customer=OuterRef("pk"), type=CustomerDebt.Type.DECREASE)
            .values("customer")
            .annotate(total=Sum("amount"))
            .values("total")
        )

        qs = (
            Customer.objects
            .annotate(
                total_purchase_amount=Coalesce(
                    Subquery(total_purchase_subquery, output_field=DecimalField()), zero
                ),
                _debt_in=Coalesce(
                    Subquery(debt_in_subquery, output_field=DecimalField()), zero
                ),
                _debt_paid=Coalesce(
                    Subquery(debt_paid_subquery, output_field=DecimalField()), zero
                ),
            )
            .annotate(
                total_debt=models.ExpressionWrapper(
                    models.F("_debt_in") - models.F("_debt_paid"),
                    output_field=DecimalField(max_digits=20, decimal_places=2),
                )
            )
        )

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search) | Q(phone_number__icontains=search)
            )

        # Qarzdorlik bo'yicha: true — faqat qarzdorlar, false — qarzsizlar
        has_debt = request.query_params.get("has_debt")
        if has_debt in ("true", "1"):
            qs = qs.filter(total_debt__gt=0)
        elif has_debt in ("false", "0"):
            qs = qs.filter(total_debt__lte=0)

        ordering = request.query_params.get("ordering")
        if ordering not in self.ALLOWED_ORDERINGS:
            ordering = "full_name"
        return qs.order_by(ordering)

    def get_sheets(self, request, queryset):
        columns = [
            ("ID", 8),
            ("F.I.Sh", 30),
            ("Telefon", 16),
            ("Jami xarid", 16),
            ("Qarz", 16),
            ("Qo'shilgan sana", 17),
        ]

        def rows():
            for customer in queryset:
                yield [
                    customer.id,
                    customer.full_name,
                    customer.phone_number,
                    customer.total_purchase_amount,
                    customer.total_debt,
                    customer.created_at,
                ]

        return [("Mijozlar", columns, rows())]
