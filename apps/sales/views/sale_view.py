from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.common.i18n import tr
from apps.common.paginations import StandardPagination
from apps.debts.models import CustomerDebt
from apps.sales.models import Sale, SaleItem, Payment, SaleReturn, SaleReturnItem
from apps.sales.serializers import SaleCreateSerializer, SaleListSerializer, CustomerDebtListSerializer
from apps.sales.services import SaleService
from django.db.models import Q

from apps.sales.services import CustomerDebtService
from apps.sales.filters import SaleFilter

from django.db.models import (
    Case, Count, DecimalField, F, OuterRef,
    Prefetch, Subquery, Sum, Value, When,
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
    Sotuvlar ro'yxati sahifasidagi statistika kartalari uchun.

    Ro'yxatdan farqi: paginatsiyasiz, BUTUN filtrlangan davr bo'yicha
    yig'indilar qaytadi (avval frontend faqat joriy sahifadagi 10 ta
    yozuvdan hisoblab noto'g'ri ko'rsatardi).

    Do'kon cheklovi ro'yxat bilan bir xil: superuser hammasini,
    oddiy user faqat o'z do'kon(lar)ini ko'radi.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = _scope_to_user_stores(Sale.objects.all(), request.user)

        params = request.query_params
        store = params.get("store")
        if store:
            qs = qs.filter(store_id=store)
        date_from = params.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        date_to = params.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        totals = qs.aggregate(
            total_sales=Count("id"),
            total_amount=Coalesce(Sum("total_amount"), Value(0, output_field=DecimalField())),
            total_paid=Coalesce(Sum("paid_amount"), Value(0, output_field=DecimalField())),
        )

        # Qarz — ledger bo'yicha (kirim 'i' minus to'lov 'd'), xuddi ro'yxatdagi kabi
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

        # To'langan summaning taqsimoti: naqd + har bir bank kartasi bo'yicha
        # (filtrlangan sotuvlarning to'lovlari, qaytarimlar NET ayiriladi).
        # Eski to'lovlarda bank_card=NULL bo'lishi mumkin — frontend "Noma'lum karta" deb ko'rsatadi.
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

        # To'langan — NET (qaytarimlar ayirilgan), breakdown bilan bir manbadan.
        # Sum(sale.paid_amount) ishlatilmaydi: qaytarim paid_amount ni
        # kamaytirmaydi, shuning uchun u breakdown yig'indisiga mos kelmasdi.
        total_paid_net = sum((row["amount"] for row in paid_rows), Decimal("0"))

        # Oxirgi qarz to'lovlari — "Jami qarzdorlik" kartasi ostida ko'rsatiladi.
        # Sana filtriga bog'liq EMAS (eng oxirgilari), do'kon cheklovi saqlanadi.
        # Bitta to'lov harakati (payment_group) bitta yozuv: jami summa + qismlari
        # (naqd / har bir karta: Humo, Uzcard, ...).
        debt_scope = _scope_to_user_stores(Sale.objects.all(), request.user)
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

        # Qaytarilgan summa — davr ichida rasmiylashtirilgan qaytarimlar (SaleReturn).
        # Sotuv sanasiga emas, QAYTARIM sanasiga qarab hisoblanadi: o'tgan oygi sotuv
        # bugun qaytarilsa, bugungi statistikada ko'rinadi. Do'kon cheklovi/filtri bir xil.
        returns_qs = _scope_to_user_stores(SaleReturn.objects.all(), request.user)
        if store:
            returns_qs = returns_qs.filter(store_id=store)
        if date_from:
            returns_qs = returns_qs.filter(created_at__date__gte=date_from)
        if date_to:
            returns_qs = returns_qs.filter(created_at__date__lte=date_to)
        returned = returns_qs.aggregate(
            total=Coalesce(Sum("total_refund"), Value(0, output_field=DecimalField()))
        )

        # Davrga bog'liq BO'LMAGAN jami qaytarim (do'kon cheklovi saqlanadi) —
        # frontend kartada "Hammasi" sifatida ko'rsatiladi: tanlangan davrda
        # qaytarim bo'lmasa ham umumiy summa ko'rinib turadi
        if date_from or date_to:
            returned_all_qs = _scope_to_user_stores(SaleReturn.objects.all(), request.user)
            if store:
                returned_all_qs = returned_all_qs.filter(store_id=store)
            returned_all = returned_all_qs.aggregate(
                total=Coalesce(Sum("total_refund"), Value(0, output_field=DecimalField()))
            )
        else:
            returned_all = returned

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
                "total_returned": str(returned["total"]),
                "total_returned_all": str(returned_all["total"]),
                "paid_breakdown": paid_breakdown,
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
        qs = Sale.objects.select_related(
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

# Arxivda saqlash muddati — shu muddatdan keyin purge butunlay o'chiradi
SALE_ARCHIVE_RETENTION_DAYS = 30


def purge_expired_deleted_sales() -> int:
    """
    30 kundan oshgan arxivdagi sotuvlarni BUTUNLAY o'chiradi.
    Arxiv/o'chirish so'rovlarida chaqiriladi (lazy purge) + cron uchun
    `manage.py purge_deleted_sales` buyrug'i ham shu funksiyani ishlatadi.
    """
    cutoff = timezone.now() - timedelta(days=SALE_ARCHIVE_RETENTION_DAYS)
    expired_ids = list(
        Sale.all_objects.filter(deleted_at__lt=cutoff).values_list("id", flat=True)
    )
    if not expired_ids:
        return 0
    with transaction.atomic():
        # PROTECT zanjiri (SaleReturnItem → SaleItem): avval qaytarim yozuvlari o'chiriladi
        SaleReturnItem.objects.filter(sale_return__sale_id__in=expired_ids).delete()
        SaleReturn.objects.filter(sale_id__in=expired_ids).delete()
        # Qarz ledgeri SET_NULL — yetim yozuv qolmasligi uchun sotuv bilan birga o'chadi
        CustomerDebt.objects.filter(sale_id__in=expired_ids).delete()
        # items/payments — CASCADE
        Sale.all_objects.filter(id__in=expired_ids).delete()
    return len(expired_ids)


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
        now = timezone.now()
        results = []
        for sale in qs:
            elapsed_days = (now - sale.deleted_at).days
            results.append({
                "id": sale.id,
                "store_name": sale.store.name if sale.store_id else None,
                "customer_name": sale.customer.full_name if sale.customer_id else None,
                "total_amount": str(sale.total_amount),
                "paid_amount": str(sale.paid_amount),
                "created_at": sale.created_at,
                "deleted_at": sale.deleted_at,
                "days_left": max(0, SALE_ARCHIVE_RETENTION_DAYS - elapsed_days),
            })
        return Response(
            {"results": results, "retention_days": SALE_ARCHIVE_RETENTION_DAYS},
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
