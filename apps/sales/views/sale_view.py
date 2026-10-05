from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.common.i18n import tr
from apps.common.paginations import StandardPagination
from apps.debts.models import CustomerDebt
from apps.sales.models import Sale, SaleItem, Payment, SaleReturn, SaleReturnItem
from apps.sales.serializers import SaleCreateSerializer, SaleListSerializer, CustomerDebtListSerializer
from apps.sales.services import SaleService, CustomerDebtService
from apps.sales.filters import SaleFilter
from django.db.models import (
    Case, Count, DecimalField, ExpressionWrapper, F, OuterRef,
    Prefetch, Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics, serializers
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

# ─────────────────────────────────────────────
# YORDAMCHI SUBQUERYLAR
# ─────────────────────────────────────────────

def _debt_increase_subquery() -> Subquery:
    """
    Har bir Sale uchun umumiy qarzdorlik (type='i') ni Subquery orqali hisoblaydi.

    Nima uchun Subquery?
      Avvalgi kod:
        qs.annotate(total_increase=Coalesce(Sum(Case(When(debt_records__type='i', ...)))))
      Muammo:
        Sale → items (ko'p) va Sale → debt_records (ko'p) bir vaqtda JOIN bo'lganda
        kartezian ko'payish yuz beradi:
          3 ta item × 2 ta debt_record = 6 qator → Sum ikki baravar katta chiqadi ❌

      Subquery esa asosiy querydan AJRALGAN ishlaydi — kartezian yo'q,
      har Sale uchun to'g'ri qiymat qaytaradi ✅
    """
    return Coalesce(
        Subquery(
            CustomerDebt.objects.filter(sale=OuterRef("pk"), type="i")
            .values("sale")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


def _debt_decrease_subquery() -> Subquery:
    """Har bir Sale uchun qarz kamayishi (type='d') ni Subquery orqali hisoblaydi."""
    return Coalesce(
        Subquery(
            CustomerDebt.objects.filter(sale=OuterRef("pk"), type="d")
            .values("sale")
            .annotate(total=Sum("amount"))
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


def _scope_to_user_stores(qs, user):
    """
    Do'kon darajasidagi cheklov: superuser hamma sotuvlarni ko'radi,
    oddiy user faqat O'Z DO'KON(LAR)IDAGI sotuvlarni ko'radi
    (avval seller=user edi — do'kondagi boshqa sotuvchilarning sotuvlari
    ko'rinmasdi; endi do'kon bo'yicha).
    """
    if user.is_superuser:
        return qs
    from apps.store.models import StoreUser

    store_ids = StoreUser.objects.filter(
        user=user, is_active=True
    ).values_list("store_id", flat=True)
    return qs.filter(store_id__in=list(store_ids))


# ─────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────

@extend_schema(
    tags=["Sales"],
    summary="Sotuv ro'yxati",
    parameters=[
        OpenApiParameter("search", OpenApiTypes.STR,
                         description="Mijoz ismi bo'yicha qidirish"),
        OpenApiParameter("status", OpenApiTypes.STR,
                         description="Holat: paid, partial, debt, r"),
        OpenApiParameter("store", OpenApiTypes.INT,
                         description="Do'kon ID"),
        OpenApiParameter("customer", OpenApiTypes.INT,
                         description="Mijoz ID"),
        OpenApiParameter("date_from", OpenApiTypes.DATE,
                         description="Sana oralig'i boshi (YYYY-MM-DD)"),
        OpenApiParameter("date_to", OpenApiTypes.DATE,
                         description="Sana oralig'i oxiri (YYYY-MM-DD)"),
        OpenApiParameter("ordering", OpenApiTypes.STR,
                         description="-created_at, total_amount, -total_amount"),
        OpenApiParameter("page", OpenApiTypes.INT, description="Sahifa raqami"),
        OpenApiParameter("limit", OpenApiTypes.INT, description="Sahifadagi yozuvlar soni"),
    ],
)
class SaleListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleListSerializer
    pagination_class = StandardPagination

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SaleFilter
    search_fields = ["customer__full_name"]
    ordering_fields = ["created_at", "total_amount"]
    ordering = ["-created_at"]

    def get_queryset(self):
        user = self.request.user

        # ✅ YAXSHI: `SaleItem` uchun `product` select_related bilan prefetch qilinyapti, item serializer N+1 dan himoyalangan.
        items_qs = SaleItem.objects.select_related("product")

        qs = (
            Sale.objects
            .select_related("store", "customer", "seller")
            .prefetch_related(
                Prefetch("items", queryset=items_qs),
                Prefetch("payments", queryset=Payment.objects.select_related("bank_card").order_by("created_at")),
            )
            .annotate(
                # ✅ YAXSHI: Qarz yig'indilari reverse FK join bilan emas, `Subquery` orqali hisoblangan.
                # Bu `items` va `debt_records` bir vaqtda join bo'lganda kartezian ko'payishdan saqlaydi.
                total_increase=_debt_increase_subquery(),
                total_decrease=_debt_decrease_subquery(),
            )
        )

        # 🔐 PERMISSION: superuser barcha sotuvlarni ko'radi,
        # oddiy foydalanuvchi faqat o'z do'kon(lar)idagi sotuvlarni.
        return _scope_to_user_stores(qs, user)



# @extend_schema(
#     tags=['Sales'],
#     summary="Sotuv ro'yxati",
# )
# class SaleListAPIView(generics.ListAPIView):
#     permission_classes = [IsAuthenticated]
#     serializer_class = SaleListSerializer
#     pagination_class = StandardPagination
#
#     filter_backends = [DjangoFilterBackend]
#     filterset_class = SaleFilter
#
#     def get_queryset(self):
#         user = self.request.user
#
#         qs = Sale.objects.select_related(
#             "store", "customer", "seller"
#         ).prefetch_related("items")
#         # N+1 tuzatish: yuqoridagi `items` prefetchiga `Prefetch(..., queryset=SaleItem.objects.select_related("product"))`
#         # qo'shish tavsiya etiladi — `SaleItemSerializer.get_product_name` uchun.
#
#         # 🔐 PERMISSION
#         if not user.is_superuser:
#             qs = qs.filter(seller=user)
#
#         # 🔥 LEDGER BASED DEBT
#         # SQL / mantiq: `Sum` bilan bog'langan reverse FK (`debt_records`) boshqa `annotate`lar
#         # yoki joinlar bilan aralashsa kartezian ko'payish va noto'g'ri yig'indi xavfi bor — murakkab
#         # tarixda subquery yoki alohida hisoblash strategiyasini ko'rib chiqish tavsiya etiladi.
#         qs = qs.annotate(
#
#             total_increase=Coalesce(Sum(
#                 Case(
#                     When(
#                         debt_records__type="i",
#                         then=F("debt_records__amount")
#                     ),
#                     output_field=DecimalField()
#                 )
#             ), Value(0, output_field=DecimalField())),
#
#             total_decrease=Coalesce(Sum(
#                 Case(
#                     When(
#                         debt_records__type="d",
#                         then=F("debt_records__amount")
#                     ),
#                     output_field=DecimalField()
#                 )
#             ), Value(0, output_field=DecimalField())),
#         )
#
#         return qs.order_by("-created_at")


def _parse_date_bounds(date_from: str | None, date_to: str | None) -> tuple[datetime | None, datetime | None]:
    """
    Sana oralig'ini [start, end) yarim-ochiq datetime chegaralarga aylantiradi.
    created_at indeksi to'liq ishlashi (sargable) va kun oxiri to'liq qamrab olinishi uchun.
    """
    tz = timezone.get_current_timezone()
    start_dt = None
    end_dt = None
    if date_from:
        if isinstance(date_from, datetime):
            start_dt = date_from
        elif isinstance(date_from, date):
            start_dt = datetime.combine(date_from, time.min)
        else:
            try:
                d = date.fromisoformat(str(date_from).strip())
                start_dt = datetime.combine(d, time.min)
            except (ValueError, TypeError):
                start_dt = None
        if start_dt and timezone.is_naive(start_dt):
            start_dt = timezone.make_aware(start_dt, tz)

    if date_to:
        if isinstance(date_to, datetime):
            end_dt = date_to
        elif isinstance(date_to, date):
            end_dt = datetime.combine(date_to + timedelta(days=1), time.min)
        else:
            try:
                d = date.fromisoformat(str(date_to).strip())
                end_dt = datetime.combine(d + timedelta(days=1), time.min)
            except (ValueError, TypeError):
                end_dt = None
        if end_dt and timezone.is_naive(end_dt):
            end_dt = timezone.make_aware(end_dt, tz)

    return start_dt, end_dt


@extend_schema(
    tags=["Sales"],
    summary="Sotuv statistikasi (filtrlangan davr bo'yicha jami)",
    parameters=[
        OpenApiParameter("store", OpenApiTypes.INT, description="Do'kon ID (faqat superuser uchun ma'noli)"),
        OpenApiParameter("date_from", OpenApiTypes.DATE, description="Sana oralig'i boshi (YYYY-MM-DD)"),
        OpenApiParameter("date_to", OpenApiTypes.DATE, description="Sana oralig'i oxiri (YYYY-MM-DD)"),
    ],
)
class SaleStatisticsAPIView(APIView):
    """
    Sotuvlar ro'yxati sahifasidagi statistika kartalari uchun (Period Transactional Accounting).

    Ro'yxatdan farqi: paginatsiyasiz, BUTUN filtrlangan davr bo'yicha
    yig'indilar qaytadi. Sotuvlar va qaytarimlar mustaqil tranzaksiyalar oqimi
    (dual transaction streams) orqali hisoblanadi.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        store = params.get("store")
        date_from = params.get("date_from")
        date_to = params.get("date_to")

        start_dt, end_dt = _parse_date_bounds(date_from, date_to)

        # 1. SALES STREAM (Sotuvlar oqimi)
        # Soft-deleted sotuvlar chiqarib tashlanadi, ammo Sale.Status.RETURNED
        # sotuv davri hisobidan CHIQARILMAYDI (mustaqil tranzaksiya oqimi).
        qs = _scope_to_user_stores(Sale.objects.filter(deleted_at__isnull=True), request.user)
        if store:
            qs = qs.filter(store_id=store)
        if start_dt:
            qs = qs.filter(created_at__gte=start_dt)
        elif date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if end_dt:
            qs = qs.filter(created_at__lt=end_dt)
        elif date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        totals = qs.aggregate(
            total_sales=Count("id"),
            total_amount=Coalesce(Sum("total_amount"), Value(Decimal("0.00")), output_field=DecimalField()),
            total_paid=Coalesce(Sum("paid_amount"), Value(Decimal("0.00")), output_field=DecimalField()),
        )
        sold_revenue = totals["total_amount"]

        # Sotilgan tovarlar tannarxi (COGS) va yalpi foyda
        sale_items = SaleItem.objects.filter(sale__in=qs)
        sold_cogs = sale_items.aggregate(
            cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F("quantity") * Coalesce(F("purchase_price"), Value(Decimal("0.00")), output_field=DecimalField()),
                        output_field=DecimalField(),
                    )
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            )
        )["cost"]
        sold_profit = sold_revenue - sold_cogs

        # 2. RETURNS STREAM (Qaytarimlar oqimi — aynan shu davrda amalga oshirilgan SaleReturn)
        returns_qs = _scope_to_user_stores(
            SaleReturn.objects.filter(sale__deleted_at__isnull=True),
            request.user,
        )
        if store:
            returns_qs = returns_qs.filter(store_id=store)
        if start_dt:
            returns_qs = returns_qs.filter(created_at__gte=start_dt)
        elif date_from:
            returns_qs = returns_qs.filter(created_at__date__gte=date_from)
        if end_dt:
            returns_qs = returns_qs.filter(created_at__lt=end_dt)
        elif date_to:
            returns_qs = returns_qs.filter(created_at__date__lte=date_to)

        returned = returns_qs.aggregate(
            total=Coalesce(Sum("total_refund"), Value(Decimal("0.00")), output_field=DecimalField())
        )
        return_items = SaleReturnItem.objects.filter(sale_return__in=returns_qs)

        # total_refund 0 bo'lsa SaleReturnItem.total_price ga fallback
        if returned["total"] == Decimal("0.00"):
            ret_items_total = return_items.aggregate(
                total=Coalesce(Sum("total_price"), Value(Decimal("0.00")), output_field=DecimalField())
            )["total"]
            if ret_items_total > Decimal("0.00"):
                returned["total"] = ret_items_total

        # Qaytarilgan tovarlar tannarxi (tarixiy COGS: SaleReturnItem.sale_item.purchase_price)
        return_cogs = return_items.aggregate(
            cost=Coalesce(
                Sum(
                    ExpressionWrapper(
                        F("quantity") * Coalesce(F("sale_item__purchase_price"), Value(Decimal("0.00")), output_field=DecimalField()),
                        output_field=DecimalField(),
                    )
                ),
                Value(Decimal("0.00")),
                output_field=DecimalField(),
            )
        )["cost"]

        return_revenue = returned["total"]
        return_lost_profit = return_revenue - return_cogs

        # 3. PERIOD TRANSACTIONAL NETTING
        total_profit = (sold_profit - return_lost_profit).quantize(Decimal("0.01"))

        # Tannarxi yozilmagan qatorlar bo'lsa foyda taxminiy ekanligini belgilash
        profit_partial = (
            sale_items.filter(Q(purchase_price__isnull=True) | Q(purchase_price=0)).exists()
            or return_items.filter(Q(sale_item__purchase_price__isnull=True) | Q(sale_item__purchase_price=0)).exists()
        )

        # 4. CUSTOMER DEBT (Ledger bo'yicha: kirim 'i' minus to'lov 'd')
        debt = CustomerDebt.objects.filter(sale__in=qs.values("id")).aggregate(
            total=Coalesce(
                Sum(
                    Case(
                        When(type="i", then=F("amount")),
                        When(type="d", then=-F("amount")),
                        default=Value(0),
                        output_field=DecimalField(),
                    )
                ),
                Value(0, output_field=DecimalField()),
            )
        )

        # 5. PAYMENT BREAKDOWN (To'langan summaning taqsimoti: naqd + har bir karta)
        paid_rows = (
            Payment.objects
            .filter(sale__in=qs.values("id"))
            .values("type", "bank_card__name")
            .annotate(
                amount=Coalesce(
                    Sum(
                        Case(
                            When(is_refund=True, then=-F("amount")),
                            default=F("amount"),
                            output_field=DecimalField(),
                        )
                    ),
                    Value(0, output_field=DecimalField()),
                )
            )
            .order_by("-amount")
        )
        paid_breakdown = [
            {
                "type": row["type"],
                "name": row["bank_card__name"],
                "amount": str(row["amount"]),
            }
            for row in paid_rows
            if row["amount"]
        ]
        total_paid_net = sum((row["amount"] for row in paid_rows), Decimal("0"))

        # 6. RECENT DEBT PAYMENTS
        debt_scope = _scope_to_user_stores(Sale.objects.filter(deleted_at__isnull=True), request.user)
        if store:
            debt_scope = debt_scope.filter(store_id=store)
        recent_rows = list(
            Payment.objects
            .filter(is_debt_payment=True, is_refund=False, sale__in=debt_scope.values("id"))
            .select_related("bank_card")
            .order_by("-created_at", "-id")[:15]
        )
        recent_groups: dict[str, list] = {}
        recent_order: list[str] = []
        for p in recent_rows:
            key = str(p.payment_group) if p.payment_group else f"solo-{p.id}"
            if key not in recent_groups:
                recent_groups[key] = []
                recent_order.append(key)
            recent_groups[key].append(p)
        recent_debt_payments = [
            {
                "sale": group_rows[0].sale_id,
                "created_at": group_rows[0].created_at.isoformat(),
                "amount": str(sum((r.amount for r in group_rows), Decimal("0"))),
                "parts": [
                    {
                        "type": r.type,
                        "name": r.bank_card.name if r.bank_card else None,
                        "amount": str(r.amount),
                    }
                    for r in group_rows
                ],
            }
            for group_rows in (recent_groups[k] for k in recent_order[:4])
        ]

        # 7. ALL-TIME RETURNS (Frontend kartada "Hammasi" ko'rsatkichi uchun)
        if date_from or date_to:
            returned_all_qs = _scope_to_user_stores(
                SaleReturn.objects.filter(sale__deleted_at__isnull=True),
                request.user,
            )
            if store:
                returned_all_qs = returned_all_qs.filter(store_id=store)
            returned_all = returned_all_qs.aggregate(
                total=Coalesce(Sum("total_refund"), Value(Decimal("0.00")), output_field=DecimalField())
            )
            if returned_all["total"] == Decimal("0.00"):
                all_ret_items_total = SaleReturnItem.objects.filter(sale_return__in=returned_all_qs).aggregate(
                    total=Coalesce(Sum("total_price"), Value(Decimal("0.00")), output_field=DecimalField())
                )["total"]
                if all_ret_items_total > Decimal("0.00"):
                    returned_all["total"] = all_ret_items_total
        else:
            returned_all = returned

        # 8. REFUND BREAKDOWN
        refund_rows_qs = Payment.objects.filter(
            is_refund=True,
            sale__in=debt_scope.values("id"),
        )
        if start_dt:
            refund_rows_qs = refund_rows_qs.filter(created_at__gte=start_dt)
        elif date_from:
            refund_rows_qs = refund_rows_qs.filter(created_at__date__gte=date_from)
        if end_dt:
            refund_rows_qs = refund_rows_qs.filter(created_at__lt=end_dt)
        elif date_to:
            refund_rows_qs = refund_rows_qs.filter(created_at__date__lte=date_to)

        refund_rows = (
            refund_rows_qs
            .values("type", "bank_card__name")
            .annotate(amount=Coalesce(Sum("amount"), Value(0, output_field=DecimalField())))
            .order_by("-amount")
        )
        returned_breakdown = [
            {
                "type": row["type"],
                "name": row["bank_card__name"],
                "amount": str(row["amount"]),
            }
            for row in refund_rows
            if row["amount"]
        ]

        # 9. RESPONSE CONTRACT
        return Response(
            {
                "total_sales": totals["total_sales"],
                # Yalpi savdo (qaytarimlardan OLDIN) — moslik uchun saqlanadi
                "total_amount": str(totals["total_amount"]),
                # Sof savdo: davr sotuvlari - davr ichidagi qaytarimlar
                "total_net": str(totals["total_amount"] - returned["total"]),
                # NET to'langan (qaytarib berilgan pul ayirilgan) — breakdown bilan mos
                "total_paid": str(total_paid_net),
                "total_debt": str(debt["total"]),
                # Sof foyda (davr + do'kon filtri bo'yicha) va u to'liqmi
                "total_profit": str(total_profit),
                "profit_partial": profit_partial,
                "total_returned": str(returned["total"]),
                "total_returned_all": str(returned_all["total"]),
                "paid_breakdown": paid_breakdown,
                "returned_breakdown": returned_breakdown,
                "recent_debt_payments": recent_debt_payments,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=['Sales'],
    summary="Sotuv yaratish",
)
class SaleCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleCreateSerializer

    def post(self, request):

        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        sale = SaleService.create_sale(
            user=request.user,
            data=serializer.validated_data
        )

        return Response({
            "sale_id": sale.id,
            "total": sale.total_amount,
            "paid": sale.paid_amount,
            "status": sale.status
        }, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=['Sales'],
    summary="ID orqali Sotuv malumotlarini olish",
)
class SaleDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = SaleListSerializer

    def get(self, request, pk):
        # Ro'yxat va statistika view'lari scoping qiladi, detail esa qilmasdi —
        # sotuvchi ID'ni ketma-ket sinab butun kompaniyaning sotuvlarini
        # (mijoz, narx, to'lov tafsilotlari bilan) o'qiy olardi.
        qs = _scope_to_user_stores(Sale.objects.all(), request.user).select_related(
            "store", "customer", "seller"
        ).prefetch_related(
            Prefetch("items", queryset=SaleItem.objects.select_related("product")),
            Prefetch("payments", queryset=Payment.objects.select_related("bank_card").order_by("created_at")),
        ).annotate(
        # ⚠️ MUAMMO [KRITIK]: Detail querysetda `items` va `debt_records` aggregate birga ishlatilgan.
        # Sabab: `prefetch_related("items")` productni olib kelmaydi, `Sum(debt_records...)` esa reverse FK joinlarga
        # boshqa joinlar qo'shilsa kartezian ko'payish xavfini saqlab qoladi.
        # Natija: qarz summasi noto'g'ri chiqishi va item product nomida N+1 yuzaga kelishi mumkin.
        # ✅ YECHIM:
        # qs = (
        #     Sale.objects.select_related("store", "customer", "seller")
        #     .prefetch_related(Prefetch("items", queryset=SaleItem.objects.select_related("product")))
        #     .annotate(total_increase=_debt_increase_subquery(), total_decrease=_debt_decrease_subquery())
        # )
        # N+1: bitta sotuvda ham `items__product` prefetch (`Prefetch` + `select_related("product")`) tavsiya.
        # SQL: `debt_records` annotate bilan kartezian ko'payish xavfi (ro'yxat view bilan bir xil).

            total_increase=Coalesce(Sum(
                Case(
                    When(
                        debt_records__type="i",
                        then=F("debt_records__amount")
                    ),
                    output_field=DecimalField()
                )
            ), Value(0, output_field=DecimalField())),

            total_decrease=Coalesce(Sum(
                Case(
                    When(
                        debt_records__type="d",
                        then=F("debt_records__amount")
                    ),
                    output_field=DecimalField()
                )
            ), Value(0, output_field=DecimalField())),
        )

        sale = get_object_or_404(qs, pk=pk)

        serializer = self.serializer_class(sale)
        return Response(serializer.data, status=status.HTTP_200_OK)


# =============================================================================

@extend_schema(
    tags=['Sales'],
    summary="Qarzdor mijozlar ro'yxati"
)
class CustomerDebtListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CustomerDebtListSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        return CustomerDebtService.get_customer_debts(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        # ✅ YAXSHI: Qarzdor mijozlar ro'yxati pagination orqali qaytariladi.
        page = self.paginate_queryset(queryset)

        formatted_data = CustomerDebtService.format_debt_response(
            data=page
        )

        return self.get_paginated_response(formatted_data)


# ─────────────────────────────────────────────
# SOTUVLARNI O'CHIRISH (ARXIV, faqat superadmin)
# ─────────────────────────────────────────────

# Arxivda saqlash muddati — Indefinite soft-delete retention (fizik o'chirilmaydi).
# Tarixiy buxgalteriya, FIFO va StockAllocation daxlsizligini saqlash uchun
# sotuvlar bazadan BUTUNLAY O'CHIRILMAYDI.
SALE_ARCHIVE_RETENTION_DAYS = None


def purge_expired_deleted_sales() -> int:
    """
    Indefinite Soft-Delete Retention Policy:
    Tarixiy hisobotlar, FIFO va StockAllocation daxlsizligini saqlash maqsadida
    arxivlangan (soft-deleted) sotuvlar bazadan BUTUNLAY O'CHIRILMAYDI.
    Ular deleted_at orqali operatsion so'rovlardan chiqarilgan holda daxlsiz saqlanadi.
    Backward-compatibility uchun 0 qaytaradi.
    """
    return 0


@extend_schema(
    tags=["Sales"],
    summary="Sotuvlarni o'chirish (arxivga) — faqat superadmin",
)
class SaleBulkDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": tr("only_superuser_sales_delete")},
                status=status.HTTP_403_FORBIDDEN,
            )
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            return Response(
                {"detail": tr("no_sales_selected")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        purge_expired_deleted_sales()
        archived = Sale.objects.filter(id__in=ids).update(deleted_at=timezone.now())
        return Response({"archived": archived}, status=status.HTTP_200_OK)


@extend_schema(
    tags=["Sales"],
    summary="O'chirilgan sotuvlar arxivi — faqat superadmin",
)
class SaleArchiveListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": tr("only_superuser_sales_delete")},
                status=status.HTTP_403_FORBIDDEN,
            )
        purge_expired_deleted_sales()
        qs = (
            Sale.all_objects
            .filter(deleted_at__isnull=False)
            .select_related("store", "customer")
            .order_by("-deleted_at")
        )
        results = []
        for sale in qs:
            results.append({
                "id": sale.id,
                "store_name": sale.store.name if sale.store_id else None,
                "customer_name": sale.customer.full_name if sale.customer_id else None,
                "total_amount": str(sale.total_amount),
                "paid_amount": str(sale.paid_amount),
                "created_at": sale.created_at,
                "deleted_at": sale.deleted_at,
                "days_left": None,
            })
        return Response(
            {"results": results, "retention_days": None},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Sales"],
    summary="Arxivdagi sotuvlarni tiklash — faqat superadmin",
)
class SaleRestoreAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not request.user.is_superuser:
            return Response(
                {"detail": tr("only_superuser_sales_delete")},
                status=status.HTTP_403_FORBIDDEN,
            )
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            return Response(
                {"detail": tr("no_sales_selected")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        restored = (
            Sale.all_objects
            .filter(id__in=ids, deleted_at__isnull=False)
            .update(deleted_at=None)
        )
        return Response({"restored": restored}, status=status.HTTP_200_OK)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SaleDetailAPIView annotate strategiyasini list viewdagi Subquery bilan bir xil qilish]
# ═══════════════════════════════
