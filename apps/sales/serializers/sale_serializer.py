from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from apps.sales.models import (
    BankCard,
    Sale,
    SaleItem,
    Payment,
)
from apps.sales.services.payment_service import validate_payment_method
from apps.store.models import StoreUser

from decimal import Decimal

# ─────────────────────────────────────────────
# SERIALIZERS
# ─────────────────────────────────────────────

class PaymentSerializer(serializers.ModelSerializer):
    # N+1 yo'q: view querysetida payments prefetch'i select_related("bank_card") bilan keladi
    bank_card_name = serializers.CharField(source="bank_card.name", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ("id", "amount", "type", "bank_card", "bank_card_name", "is_refund", "payment_group", "created_at")


class SaleItemSerializer(serializers.ModelSerializer):
    """
    N+1 tuzatish:
      Avval → get_product_name() metodi obj.product.name ga murojaat qilardi,
              lekin product prefetch qilinmagan edi → har item uchun SQL.
      Keyin → get_queryset() da Prefetch("items", queryset=...select_related("product"))
              ishlatiladi, shuning uchun source= yetarli — metod shart emas.
    """
    # ✅ YAXSHI: `SerializerMethodField` o'rniga `source="product.name"` ishlatilgan.
    product_name = serializers.CharField(source="product.name", read_only=True)
    sku = serializers.CharField(source="product.sku", read_only=True, default=None)

    class Meta:
        model = SaleItem
        fields = (
            "id", "product", "product_name", "sku",
            "quantity", "unit_price", "total_price",
            "returned_quantity",
        )


class SaleListSerializer(serializers.ModelSerializer):
    """
    SerializerMethodField → source= ga o'tkazildi:
      get_store_name    → source="store.name"
      get_customer_name → source="customer.full_name"
      get_seller_name   → source="seller.full_name" (yoki get_full_name())

    Nima uchun? source= Python darajasida ishlaydi — qo'shimcha SQL yo'q
    (select_related queryset darajasida hal qilgan), va metod yozish shart emas.

    debt:
      Avval → get_debt() ichida obj.total_increase, obj.total_decrease ni o'qirdi.
              Bu annotatedan kelardi, lekin kartezian tufayli qiymatlar noto'g'ri edi.
      Keyin → Subquery annotate dan kelgan to'g'ri qiymatlar ishlatiladi.
    """
    # ✅ YAXSHI: FK nomlari `source` orqali berilgan; queryset `select_related` bo'lsa qo'shimcha SQL chiqmaydi.
    store_name = serializers.CharField(source="store.name", read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True, default=None)
    seller_name = serializers.CharField(source="seller.full_name", read_only=True, default=None)

    items = SaleItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    # Subquery annotatedan keladigan tayyor qiymatlar
    total_increase = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    total_decrease = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    # ⚠️ MUAMMO [ARXITEKTURA/PERFORMANCE]: `debt` serializer method orqali hisoblanadi.
    # Sabab: qiymat annotate qilingan maydonlardan keladi, lekin yakuniy hisob serializer qatlamida qolgan.
    # Natija: list uchun biznes formula serializerga bog'lanadi; filter/order qilish kerak bo'lsa qayta yozish talab etiladi.
    # ✅ YECHIM:
    # debt = serializers.DecimalField(max_digits=20, decimal_places=2, read_only=True)
    # Querysetda: .annotate(debt=Greatest(F("total_increase") - F("total_decrease"), Value(0)))
    debt = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            "id",
            "store", "store_name",
            "seller", "seller_name",
            "customer", "customer_name",
            "payments",
            "status",
            "payment_type",
            "total_amount", "paid_amount", "debt",
            "total_increase", "total_decrease",
            "discount_type", "discount_value", "discount_amount",
            "items",
            "created_at",
        )

    def get_debt(self, obj) -> Decimal | None:
        """
        Subquery annotatedan kelgan to'g'ri qiymatlar asosida hisoblaydi.
        Nol yoki manfiy bo'lsa None qaytaradi (qarzdorlik yo'q).
        """
        increase = obj.total_increase or Decimal("0")
        decrease = obj.total_decrease or Decimal("0")
        debt = increase - decrease
        return debt if debt > 0 else None




# class SaleItemSerializer(serializers.ModelSerializer):
#     # N+1: `SaleListAPIView` querysetida `prefetch_related("items")` bor, lekin `items__product`
#     # yuklanmagan — har bir satr uchun `product` jadvalidan qo'shimcha SELECT ketadi.
#     product_name = serializers.SerializerMethodField()
#
#     class Meta:
#         model = SaleItem
#         fields = (
#             'id', 'product', 'product_name', 'quantity', 'unit_price', 'total_price'
#         )
#
#     def get_product_name(self, obj):
#         return obj.product.name if obj.product else None


# class SaleListSerializer(serializers.ModelSerializer):
#     store_name = serializers.SerializerMethodField()
#     customer_name = serializers.SerializerMethodField()
#     seller_name = serializers.SerializerMethodField()
#     debt = serializers.SerializerMethodField()
#     items = SaleItemSerializer(many=True)
#
#     class Meta:
#         model = Sale
#         fields = (
#             'id', 'store', 'store_name', 'seller', 'seller_name', 'customer', 'customer_name',
#             'payments', 'status', 'total_amount', 'paid_amount', 'debt',
#             'discount_type', 'discount_value', 'discount_amount', 'items', 'created_at',
#         )
#
#     def get_store_name(self, obj):
#         return obj.store.name if obj.store else None
#
#     def get_customer_name(self, obj):
#         return obj.customer.full_name if obj.customer else None
#
#     def get_seller_name(self, obj):
#         return obj.seller.full_name if obj.seller else None
#
#     def get_debt(self, obj):
#         debt = (obj.total_increase or 0) - (obj.total_decrease or 0)
#         return debt if debt > 0 else None


class SaleItemInputSerializer(serializers.Serializer):
    product = serializers.IntegerField()
    quantity = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate(self, data):
        quantity = data['quantity']
        price = data['price']

        if quantity <= 0:
            raise ValidationError("Miqdor ijoboy bo'lishi kerak")

        if price <= 0:
            raise ValidationError("Narx ijoboy bo'lishi kerak")
        return data


class PaymentInputSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Payment.Type.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    # Faqat faol kartalar qabul qilinadi; validated_data da BankCard instance bo'ladi
    bank_card = serializers.PrimaryKeyRelatedField(
        queryset=BankCard.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        default=None,
    )

    def validate(self, data):
        amount = data['amount']

        if amount <= 0:
            raise ValidationError("To'lov ijoboy bo'lishi kerak")

        # type=card → bank_card majburiy, type=cash → bank_card taqiqlanadi
        validate_payment_method(data['type'], data.get('bank_card'))
        return data



class SaleCreateSerializer(serializers.Serializer):
    store = serializers.IntegerField(required=False)
    customer = serializers.IntegerField(required=False, allow_null=True)
    discount_type = serializers.ChoiceField(choices=Sale.DiscountType.choices, required=False, allow_null=True)
    discount_value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    items = SaleItemInputSerializer(many=True)
    payments = PaymentInputSerializer(many=True)
    debt_due_date = serializers.DateField(required=False, allow_null=True)

    # def validate(self, data):
    #     # ⚠️ MUAMMO [ARXITEKTURA]: Serializer ichida store permission va payment/debt biznes qoidalari aralashgan.
    #     # Sabab: validation HTTP request userga bog'lanib, domain service bilan takrorlanadigan qoidalarni saqlaydi.
    #     # Natija: serializer unit testlari og'irlashadi va boshqa entrypointlarda bir xil qoida qayta yoziladi.
    #     # ✅ YECHIM:
    #     # SaleValidationService.validate_create(user=self.context["request"].user, data=data)
    #     items = data.get('items') or []
    #     payments = data.get('payments') or []
    #     customer = data.get('customer')

    def validate(self, data):
        items = data.get('items') or []
        payments = data.get('payments') or []
        customer = data.get('customer')
        debt_due_date = data.get('debt_due_date')
        request = self.context["request"]
        user = request.user

        # 🔴 STORE LOGIC
        # Superuser istalgan do'konni tanlaydi. Oddiy xodim uchun do'kon
        # quyidagi tartibda aniqlanadi: so'rovdagi `store` → `X-Store-ID`
        # sarlavhasi → yagona biriktirilgan do'kon.
        # Ilgari bu yerda `data["store"] = None` qilinardi va servis
        # `StoreUser...first()` ni ordering'siz olardi — ikki do'konga
        # biriktirilgan sotuvchining sotuvi tasodifiy do'konga yozilardi.
        if user.is_superuser:
            if not data.get("store"):
                raise serializers.ValidationError({
                    "store": "Superuser store tanlashi kerak"
                })
        else:
            requested_store = data.get("store")
            if requested_store is None and getattr(request, "store", None) is not None:
                requested_store = request.store

            if requested_store is not None:
                store_id = requested_store.id if hasattr(requested_store, "id") else requested_store
                if not StoreUser.objects.filter(
                    user=user, store_id=store_id, is_active=True
                ).exists():
                    raise serializers.ValidationError({
                        "store": "Siz bu do'konda sotuv qila olmaysiz"
                    })
                data["store"] = requested_store
            else:
                links = list(
                    StoreUser.objects.filter(user=user, is_active=True)
                    .select_related("store")
                    .order_by("store_id")[:2]
                )
                if not links:
                    raise serializers.ValidationError({
                        "store": "Siz hech qaysi do'konga biriktirilmagansiz"
                    })
                if len(links) > 1:
                    raise serializers.ValidationError({
                        "store": "Bir nechta do'konga biriktirilgansiz — do'konni tanlang"
                    })
                data["store"] = links[0].store

        if not items:
            raise serializers.ValidationError({
                "items": "Items bo'sh bo'lmasligi kerak"
            })
        # payments bo'sh bo'lishi mumkin — to'liq qarzga sotuv (paid=0).
        # Bunda quyidagi has_debt tarmog'i mijoz va qaytarish sanasini majburiy qiladi.

        total_items_amount = sum(
            item['quantity'] * item['price'] for item in items
        )
        total_paid = sum(
            payment['amount'] for payment in payments
        )

        # 🔴 Chegirma validatsiyasi va hisoblash
        discount_type = data.get("discount_type")
        discount_value = data.get("discount_value") or Decimal("0")
        discount_amount = Decimal("0")

        if discount_type == Sale.DiscountType.PERCENTAGE:
            if discount_value > 100:
                raise serializers.ValidationError({
                    "discount_value": "Chegirma 100% dan oshmasligi kerak"
                })
            discount_amount = (total_items_amount * discount_value) / Decimal("100")
        elif discount_type == Sale.DiscountType.FIXED:
            discount_amount = discount_value

        if discount_amount > total_items_amount:
            raise serializers.ValidationError({
                "discount_value": "Chegirma umumiy summadan oshmasligi kerak"
            })

        final_total_amount = total_items_amount - discount_amount

        # 🔴 Ortiqcha to'lov validatsiyasi
        if total_paid > final_total_amount:
            raise serializers.ValidationError({
                "payments": "To'lov summasi mahsulotlarning umumiy narxidan oshib ketmasligi kerak"
            })

        # 🔴 Qarz logikasi
        has_debt = total_paid < final_total_amount

        if has_debt:
            if not customer:
                raise serializers.ValidationError({
                    "customer": "Qarzga savdo uchun mijoz majburiy"
                })

            if not debt_due_date:
                raise serializers.ValidationError({
                    "debt_due_date": "Qarzga savdo uchun qaytarish sanasi majburiy"
                })

            if debt_due_date < timezone.localdate():
                raise serializers.ValidationError({
                    "debt_due_date": "Qaytarish sanasi o'tmishda bo'lishi mumkin emas"
                })
        else:
            # Qarz yo'q bo'lsa, due_date kiritilgan bo'lsa ham e'tiborga olinmaydi
            data["debt_due_date"] = None

        return data


# ------------------------------------------------------------------------------

from rest_framework import serializers


class CustomerDebtListSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    customer_id = serializers.IntegerField()
    customer_name = serializers.CharField()
    total_debt = serializers.DecimalField(max_digits=20, decimal_places=2)


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 1
# Arxitektura muammolari: 2
# Umumiy baho: 7 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [SaleCreateSerializer ichidagi biznes validationni service qatlamiga chiqarish]
# ═══════════════════════════════
