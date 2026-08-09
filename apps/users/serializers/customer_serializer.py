from apps.common.quantity import QuantityField
from apps.debts.models import CustomerDebt
from apps.products.models import Product
from apps.users.models.customers import Customer
from apps.sales.models import Sale, SaleItem

from rest_framework import serializers
from django.db.models import Sum, Case, When, F, DecimalField # DecimalField ni import qiling
from django.db import models



class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product  # Mahsulot modelingiz
        fields = ('id', 'name', 'price')


# class SaleItemSerializer(serializers.ModelSerializer):
#     product_name = serializers.CharField(source='product.name', read_only=True)
#
#     class Meta:
#         model = SaleItem
#         fields = ('product_name', 'quantity', 'unit_price', 'total_price')



from django.db.models import (
    Case, DecimalField, F, OuterRef,
    Subquery, Sum, Value, When,
)
from django.db.models.functions import Coalesce

# ─────────────────────────────────────────────
# YORDAMCHI FUNKSIYA — Debt annotate subquery
# ─────────────────────────────────────────────

def _debt_subquery(outer_ref: str = "pk") -> Coalesce: # Subquery
    """
    Har bir Customer uchun umumiy qarzni Subquery orqali hisoblaydi.

    Nima uchun Subquery?
      - Avvalgi kodda ikki muammo bor edi:
        1. queryset darajasida annotate(annotated_total_debt=Sum(...)) —
           bu kartezian ko'payish xavfi tug'diradi (sales va debts bir vaqtda
           JOIN bo'lsa yig'indi noto'g'ri chiqadi).
        2. Serializer ichida get_total_debt() — har mijoz uchun alohida SQL (N+1).
      - Subquery ikkisini ham hal qiladi: bitta correlated subquery,
        na kartezian, na N+1.
    """
    # ✅ YAXSHI: Customer debt `Subquery` orqali hisoblanadi, serializer ichidagi N+1 aggregate bartaraf etilgan.
    return Coalesce(
        Subquery(
            CustomerDebt.objects.filter(customer=OuterRef(outer_ref))
            .values("customer")
            .annotate(
                total=Sum(
                    Case(
                        When(type="i", then=F("amount")),
                        When(type="d", then=-F("amount")),
                        default=Value(0),
                        output_field=DecimalField(),
                    )
                )
            )
            .values("total")[:1],
            output_field=DecimalField(),
        ),
        Value(0, output_field=DecimalField()),
    )


# ─────────────────────────────────────────────
# SERIALIZERS
# ─────────────────────────────────────────────

class SaleItemSerializer(serializers.ModelSerializer):
    # product.name → select_related("product") sales queryset darajasida hal qilinadi.
    product_name = serializers.CharField(source="product.name", read_only=True)
    # JSON'da raqam sifatida (string emas) — 0.5 (yarim juft) qiymatlar uchun
    quantity = QuantityField(read_only=True)

    class Meta:
        model = SaleItem
        fields = ("id", "product", "product_name", "quantity", "total_price")


# class SaleSerializer(serializers.ModelSerializer):
#     items = SaleItemSerializer(many=True, read_only=True)
#     store_name = serializers.CharField(source="store.name", read_only=True)
#
#     class Meta:
#         model = Sale
#         fields = ("id", "store_name", "total_amount", "paid_amount", "status", "created_at", "items")


# ------------------------------------------------------------------ #
#  SaleItem — ichki serializer                                         #
# ------------------------------------------------------------------ #
# class SaleItemSerializer(serializers.Serializer):
#     id = serializers.IntegerField()
#     product = serializers.IntegerField(source="product_id")
#     product_name = serializers.CharField(source="product.name")
#     quantity = serializers.IntegerField()
#     total_price = serializers.DecimalField(max_digits=20, decimal_places=2)


# ------------------------------------------------------------------ #
#  Sale — ichki serializer                                             #
# ------------------------------------------------------------------ #
class SaleSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    store_name = serializers.CharField(source="store.name")
    total_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    paid_amount = serializers.DecimalField(max_digits=20, decimal_places=2)
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    items = SaleItemSerializer(many=True, read_only=True)


# ------------------------------------------------------------------ #
#  Customer — serializerlar                                            #
# ------------------------------------------------------------------ #
class CustomerBriefSerializer(serializers.ModelSerializer):
    """
    POS (sotuv) dropdowni uchun MINIMAL serializer.

    Hech qanday agregat/relation yo'q — ?brief=1 rejimida ishlatiladi,
    javob har doim yengil va tez bo'ladi.
    """

    class Meta:
        model = Customer
        fields = ("id", "full_name", "phone_number")


class CustomerListSerializer(serializers.ModelSerializer):
    """
    Ro'yxat (jadval) serializeri — faqat jadvalda ko'rinadigan maydonlar.

    total_purchase_amount / total_debt:
      Annotate orqali correlated Subquery — N+1 yo'q.

    DIQQAT: sales va store_debts bu yerda YO'Q — ular sahifadagi jadvalda
    ishlatilmaydi va har mijoz uchun butun sotuv tarixini yuklash ro'yxatni
    sekinlashtirardi. To'liq ma'lumot detail endpointida (CustomerSerializer).
    """

    # annotate orqali kelgan qiymatlar — SerializerMethodField shart emas
    total_purchase_amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, read_only=True
    )
    total_debt = serializers.DecimalField(
        max_digits=20, decimal_places=2, read_only=True
    )

    class Meta:
        model = Customer
        fields = (
            "id",
            "full_name",
            "phone_number",
            "total_purchase_amount",
            "total_debt",
        )


class CustomerSerializer(serializers.ModelSerializer):
    """
    N+1 muammolari qanday hal qilindi:

    total_debt:
      Avval → get_total_debt() har mijoz uchun alohida SQL yuborardi.
      Keyin → get_queryset()dagi annotate(total_debt=_debt_subquery()) dan o'qiydi.
              SerializerMethodField shart emas — source="total_debt" yetarli.

    store_debts:
      Avval → get_store_debts() har mijoz uchun alohida SQL yuborardi (N+1).
      Keyin → prefetch_related("debts") bilan debts ma'lumotlari xotirada bor,
              Python darajasida guruhlash amalga oshiriladi — SQL yo'q.

    sales → items → product:
      Avval → prefetch yo'q edi, har mijoz → har sale → har item uchun SQL.
      Keyin → Prefetch("sales") + Prefetch("sales__items") +
              select_related("product") bilan 3 ta qo'shimcha SQL (jami, N ga bog'liq emas).
    """

    # annotate orqali kelgan qiymatni to'g'ridan o'qiymiz — metod shart emas.
    total_debt = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    # Detail queryset (_customer_queryset) annotate qiladi — ro'yxatdan olib
    # tashlangani uchun detail dialog shu maydonni bu yerdan oladi.
    total_purchase_amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, read_only=True
    )
    store_debts = serializers.SerializerMethodField()
    sales = SaleSerializer(many=True, read_only=True)

    class Meta:
        model = Customer
        fields = ("id", "full_name", "phone_number", "total_debt", "total_purchase_amount", "store_debts", "sales")

    def get_store_debts(self, obj):
        """
        prefetch_related("debts__sale__store") orqali debts xotirada mavjud.
        Bu yerda SQL yo'q — Python darajasida guruhlash.
        """
        # ⚠️ MUAMMO [PERFORMANCE]: `obj.debts.all()` prefetch mavjudligiga bog'langan.
        # Sabab: serializer boshqa viewda prefetchsiz ishlatilsa har customer uchun debts query chiqadi.
        # Natija: qayta foydalanishda N+1 muammosi qaytadi.
        # ✅ YECHIM:
        # debts = getattr(obj, "_prefetched_objects_cache", {}).get("debts", [])
        # yoki store_debts ni queryset/service qatlamida tayyor annotate/json sifatida berish.
        store_map: dict[str, int | float] = {}
        for debt in obj.debts.all():
            store_name = getattr(getattr(debt, "sale", None) and debt.sale.store, "name", None)
            if not store_name:
                continue
            delta = debt.amount if debt.type == "i" else -debt.amount
            store_map[store_name] = store_map.get(store_name, 0) + delta

        return [
            {"store": store, "debt": debt}
            for store, debt in sorted(store_map.items())
        ]


class CustomerWriteSerializer(serializers.ModelSerializer):
    """
    Yaratish va yangilash uchun alohida serializer.

    Nima uchun ajratildi?
      CustomerSerializer da read_only maydonlar ko'p (total_debt, store_debts, sales).
      Yozish paytida faqat full_name va phone_number kerak — boshqalari shart emas.
      Bitta serializer ishlatilsa DRF write paytida keraksiz validatsiya qiladi.

    Xato xabarlari o'zbekcha — frontend ularni to'g'ridan-to'g'ri ko'rsatadi.
    """

    full_name = serializers.CharField(
        max_length=100,
        error_messages={
            "required": "Ism-familiya kiritilishi shart.",
            "blank": "Ism-familiya kiritilishi shart.",
            "max_length": "Ism-familiya 100 belgidan oshmasligi kerak.",
        },
    )
    phone_number = serializers.CharField(
        max_length=20,
        error_messages={
            "required": "Telefon raqam kiritilishi shart.",
            "blank": "Telefon raqam kiritilishi shart.",
            "max_length": "Telefon raqam juda uzun.",
        },
    )

    class Meta:
        model = Customer
        fields = ("id", "full_name", "phone_number")

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Ism-familiya kiritilishi shart.")
        return value

    def validate_phone_number(self, value):
        import re

        value = value.strip().replace(" ", "")
        if not re.fullmatch(r"\+?\d{7,15}", value):
            raise serializers.ValidationError(
                "Telefon raqam faqat raqamlardan iborat bo'lishi kerak (masalan: +998901234567)."
            )
        # Dublikat tekshiruvi — bir xil raqamli mijoz ikki marta yaratilmasin
        qs = Customer.objects.filter(phone_number=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Bu telefon raqamli mijoz allaqachon mavjud.")
        return value



# class CustomerSerializer(serializers.ModelSerializer):
#     # N+1: ro'yxatda har bir `Customer` uchun `get_total_debt` va `get_store_debts` alohida SQL
#     # ishga tushadi; viewdagi annotate bilan birlashtirib yagona so'rovga o'tkazish mumkin.
#     total_debt = serializers.SerializerMethodField()
#     store_debts = serializers.SerializerMethodField()
#     sales = SaleSerializer(many=True, read_only=True)
#
#     class Meta:
#         model = Customer
#         fields = ('id', 'full_name', 'phone_number', 'total_debt', 'store_debts', 'sales')
#
#     def get_total_debt(self, obj):
#         debts = obj.debts.aggregate(
#             total=Sum(
#                 Case(
#                     When(type='i', then=F('amount')),
#                     When(type='d', then=-F('amount')),
#                     default=0,
#                     output_field=models.DecimalField()
#                 )
#             )
#         )
#         return debts['total'] or 0
#
#     def get_store_debts(self, obj):
#         debts = obj.debts.values('sale__store__name').annotate(
#             store_debt=Sum(
#                 Case(
#                     When(type='i', then=F('amount')),
#                     When(type='d', then=-F('amount')),
#                     default=0,
#                     output_field=models.DecimalField()
#                 )
#             )
#         ).order_by('sale__store__name')
#
#         return [
#             {
#                 "store": item['sale__store__name'],
#                 "debt": item['store_debt']
#             }
#             for item in debts if item['sale__store__name']
#         ]


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 8 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [CustomerSerializer store_debts prefetch couplingini aniq shartnoma yoki service outputga o'tkazish]
# ═══════════════════════════════
