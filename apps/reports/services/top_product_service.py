from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.reports.services.store_scope_service import StoreScopeService
from apps.sales.models import SaleItem, SaleReturnItem


def _normalize_bounds(date_from, date_to) -> tuple[datetime, datetime]:
    """
    Sana oralig'ini [start, end) yarim-ochiq datetime chegaralarga aylantiradi.
    DateValidator, DateRangeResolver, va ISO date obyektlari bilan bir xil ishlaydi.
    """
    if isinstance(date_from, datetime):
        start = date_from
    elif isinstance(date_from, date):
        start = datetime.combine(date_from, time.min)
    else:
        start = timezone.now() - timedelta(days=30)

    if isinstance(date_to, datetime):
        if date_to.hour == 23 and date_to.minute == 59:
            end = datetime.combine(date_to.date() + timedelta(days=1), time.min)
        else:
            end = date_to
    elif isinstance(date_to, date):
        end = datetime.combine(date_to + timedelta(days=1), time.min)
    else:
        end = timezone.now()

    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)

    return start, end


class TopProductsService:

    @staticmethod
    def get_top_products(*, user, date_from, date_to, limit=5, store_id=None):
        start, end = _normalize_bounds(date_from, date_to)
        safe_limit = max(1, min(int(limit or 5), 100))

        # 1. SaleReturnItem skalyar subquery (bitta mahsulot bo'yicha davr qaytarimlari)
        returns_qs = SaleReturnItem.objects.filter(
            product_id=OuterRef("product_id"),
            sale_return__created_at__gte=start,
            sale_return__created_at__lt=end,
            sale_return__sale__deleted_at__isnull=True,
        )
        returns_qs = StoreFilterService.apply_store_filter(
            returns_qs, user, store_id, store_field="sale_return__store_id"
        )
        ret_subquery = (
            returns_qs
            .annotate(dummy=Value(1))
            .values("dummy")
            .annotate(total=Sum("quantity"))
            .values("total")[:1]
        )

        # 2. SaleItem so'rovi (barcha sotilgan tovarlar)
        sales_qs = SaleItem.objects.filter(
            sale__created_at__gte=start,
            sale__created_at__lt=end,
            sale__deleted_at__isnull=True,
        )
        sales_qs = StoreFilterService.apply_store_filter(
            sales_qs, user, store_id, store_field="sale__store_id"
        )

        # 3. Yagona SQL da agregatsiya, ayirish, filtrlash (net > 0), saralash va LIMIT
        data = (
            sales_qs
            .values("product_id", "product__name")
            .annotate(
                sold_qty=Coalesce(
                    Sum("quantity"),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
                ret_qty=Coalesce(
                    Subquery(ret_subquery, output_field=DecimalField()),
                    Value(Decimal("0")),
                    output_field=DecimalField(),
                ),
            )
            .annotate(
                net_sold_qty=ExpressionWrapper(
                    F("sold_qty") - F("ret_qty"),
                    output_field=DecimalField(),
                )
            )
            .filter(net_sold_qty__gt=0)
            .order_by("-net_sold_qty", "product_id")
            [:safe_limit]
        )

        return [
            {
                "product_id": i["product_id"],
                "name": i["product__name"],
                "total_sold": i["net_sold_qty"],
            }
            for i in data
        ]


class StoreFilterService:

    @staticmethod
    def get_permitted_store_id(user, store_id=None) -> int | list[int] | None:
        clean_store = None
        if store_id is not None and str(store_id).strip().lower() not in ("", "all", "none"):
            clean_store = int(store_id)

        if user.is_superuser:
            return clean_store

        user_store_ids = set(StoreScopeService.get_user_stores(user))
        if clean_store is not None:
            if clean_store not in user_store_ids:
                raise PermissionError("Sizda bu storega access yo‘q")
            return clean_store

        return list(user_store_ids)

    @staticmethod
    def apply_store_filter(qs, user, store_id=None, store_field="sale__store_id"):
        target = StoreFilterService.get_permitted_store_id(user, store_id)
        if target is None:
            return qs
        if isinstance(target, list):
            return qs.filter(**{f"{store_field}__in": target})
        return qs.filter(**{store_field: target})


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 3
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [limit cheklash + Sale.created_at index; behuda select_related/only ni olib tashlash]
# ═══════════════════════════════
