from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, Sum
from django.conf import settings

from apps.common.models.timestamp_mixin import TimestampMixin
from apps.sales.payment_rules import compute_payment_type
from apps.users.models.customers import Customer


class BankCard(TimestampMixin):
    """
    Kompaniyaning bank kartasi / to'lov usuli — faqat ichki hisobot uchun
    (to'lov tizimlari bilan integratsiya YO'Q, shuning uchun
    karta raqami/egasi kabi maydonlar saqlanmaydi).
    Barcha kartalar barcha filiallarda ishlaydi.

    scope — usul qaysi bo'limda ko'rinishini belgilaydi:
      sale     → faqat sotuv (kassa)
      purchase → faqat kirim (ta'minotchiga to'lov, masalan "Bank o'tkazmasi")
      both     → ikkala bo'limda ham
    """

    class Scope(models.TextChoices):
        SALE = "sale", "Faqat sotuv"
        PURCHASE = "purchase", "Faqat kirim"
        BOTH = "both", "Sotuv va kirim"

    name = models.CharField(max_length=100, unique=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    scope = models.CharField(
        max_length=10,
        choices=Scope.choices,
        default=Scope.BOTH,
        db_index=True,
    )

    class Meta:
        db_table = "bank_card"
        ordering = ["-is_default", "name"]
        constraints = [
            # DB darajasida ham faqat bitta default karta bo'lishi kafolati
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="unique_default_bank_card",
            ),
        ]

    def save(self, *args, **kwargs):
        # Yangi default tanlansa — eski defaultni avtomatik bekor qilamiz
        if self.is_default:
            with transaction.atomic():
                BankCard.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class SaleManager(models.Manager):
    # Soft-delete filtri: arxivdagi (deleted_at belgilangan) sotuvlar BARCHA standart
    # so'rovlardan (ro'yxat, statistika, hisobotlar, eksport) avtomatik chiqariladi.
    # Arxiv bilan ishlash uchun Sale.all_objects ishlatiladi.
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Sale(models.Model):

    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        PARTIAL = "partial", "Partial"
        DEBT = "debt", "Debt"
        RETURNED = "r", "Returned"

    class PaymentType(models.TextChoices):
        CASH = "cash", "Naqd"
        CARD = "card", "Karta"
        MIXED = "mixed", "Aralash"
        DEBT = "debt", "Qarz"

    class DiscountType(models.TextChoices):
        PERCENTAGE = "p", "Percentage (%)"
        FIXED = "f", "Fixed Amount"

    # PROTECT — sotuvlari bor do'konni o'chirib bo'lmaydi. CASCADE bo'lganida
    # bitta do'konni o'chirish uning barcha Sale/SaleItem/Payment yozuvlarini
    # ham olib ketardi (moliyaviy tarix qaytarib bo'lmas darajada yo'qolardi).
    store = models.ForeignKey('store.Store', on_delete=models.PROTECT, db_index=True)
    customer = models.ForeignKey(
        'users.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name='sales'
    )
    # PROTECT — xodim o'chirilganda uning sotuvlari yo'qolmasin
    # (ishdan bo'shatish moliyaviy tarixni o'chirmasligi kerak).
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    total_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    # To'lov tarkibi xulosasi — FAQAT backend hisoblaydi (recalculate_payment_type),
    # API orqali hech qachon qabul qilinmaydi. Hisobotlar shu maydon bo'yicha guruhlaydi.
    payment_type = models.CharField(
        max_length=5,
        choices=PaymentType.choices,
        default=PaymentType.DEBT,
        db_index=True,
    )
    discount_type = models.CharField(
        max_length=10,
        choices=DiscountType.choices,
        null=True,
        blank=True
    )
    discount_value = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0
    )
    discount_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0
    )

    created_at = models.DateTimeField(auto_now_add=True)

    # Soft delete (arxiv): superadmin o'chirganda darhol yo'qolmaydi — deleted_at
    # belgilanadi va sotuv ro'yxat/hisobotlardan yashiriladi. 30 kundan keyin
    # purge (_purge_expired_deleted_sales / purge_deleted_sales buyrug'i) butunlay o'chiradi.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Birinchi manager default: barcha Sale.objects so'rovlari arxivni chiqarib tashlaydi.
    # Reverse FK'lar (customer.sales, ...) Django _base_manager (filtrsiz) ishlatadi.
    objects = SaleManager()
    all_objects = models.Manager()

    class Meta:
        # Reports/ro'yxatlar deyarli har doim created_at bo'yicha filtrlaydi/saralaydi.
        # Diqqat: Meta.ordering ataylab qo'shilmagan — .values().annotate() guruhlash
        # semantikasini o'zgartirib yuborishi mumkin.
        indexes = [
            models.Index(fields=["-created_at"]),           # global ro'yxat/report oraliq filtri
            models.Index(fields=["store", "-created_at"]),  # do'kon kesimidagi report
            models.Index(fields=["seller", "-created_at"]), # sotuvchi bo'yicha ro'yxat
        ]

    def recalculate_payment_type(self, save: bool = True) -> str:
        """
        MARKAZIY metod: sotuvning payment_type ini to'lovlaridan qayta hisoblaydi.

        Barcha oqimlar (sotuv yaratish, qarz to'lash, qaytarish, admin tuzatishlar)
        AYNAN SHU metodni chaqiradi — qoida boshqa hech qayerda takrorlanmaydi.

        Hisob NET asosida: kirim to'lovlar minus shu usuldagi qaytarimlar (is_refund).
        Qoida o'zi payment_rules.compute_payment_type() da (data migration ham o'shani ishlatadi).
        """
        totals = self.payments.aggregate(
            cash_in=Sum("amount", filter=Q(type=Payment.Type.CASH, is_refund=False)),
            cash_out=Sum("amount", filter=Q(type=Payment.Type.CASH, is_refund=True)),
            card_in=Sum("amount", filter=Q(type=Payment.Type.CARD, is_refund=False)),
            card_out=Sum("amount", filter=Q(type=Payment.Type.CARD, is_refund=True)),
        )
        zero = Decimal("0")
        cash_total = (totals["cash_in"] or zero) - (totals["cash_out"] or zero)
        card_total = (totals["card_in"] or zero) - (totals["card_out"] or zero)

        self.payment_type = compute_payment_type(cash_total, card_total)

        if save:
            Sale.objects.filter(pk=self.pk).update(payment_type=self.payment_type)

        return self.payment_type

    def __str__(self):
        return f"{self.store.name} {str(self.status)}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    # PROTECT: mahsulot o'chirilganda sotuv tarixi qatorlari yo'qolmasin —
    # sotilgan mahsulot avval arxivlanadi, butunlay o'chirish esa bloklanadi
    product = models.ForeignKey('products.Product', on_delete=models.PROTECT)

    quantity = models.PositiveIntegerField()
    purchase_price = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=20, decimal_places=2)
    total_price = models.DecimalField(max_digits=20, decimal_places=2)
    returned_quantity = models.PositiveIntegerField(default=0)



class Payment(TimestampMixin):

    class Type(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"

    sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments"
    )

    # PROTECT — to'lov tarixi mijoz o'chirilganda yo'qolmasligi kerak
    # (moliyaviy yozuv; CustomerDebt.customer bilan bir xil qoida).
    customer = models.ForeignKey(
        "users.Customer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments"
    )

    amount = models.DecimalField(max_digits=20, decimal_places=2)
    type = models.CharField(max_length=5, choices=Type.choices)

    # type=card bo'lsa majburiy, type=cash bo'lsa bo'sh (clean() da tekshiriladi).
    # PROTECT — to'lovlari bor kartani o'chirib bo'lmaydi (soft delete: is_active=False).
    bank_card = models.ForeignKey(
        "sales.BankCard",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payments",
    )

    # True — mijozga QAYTARILGAN pul (sale return). Hisobotlarda kirimdan ayiriladi,
    # payment_type NET (kirim - qaytarim) asosida hisoblanadi.
    is_refund = models.BooleanField(default=False)

    # Bitta to'lov harakati (masalan, qisman qarz to'lovi naqd + Humo + Uzcard bilan)
    # bir nechta Payment qatoriga bo'linadi — ular bitta payment_group bilan bog'lanadi.
    # UI tarixda guruhni bitta to'lov bloki qilib, qismlarini ichida ko'rsatadi.
    # NULL — eski (guruhsiz) yozuvlar, har biri alohida to'lov hisoblanadi.
    payment_group = models.UUIDField(null=True, blank=True, db_index=True)

    # True — QARZ to'lovi (DebtService orqali, sotuvdan keyin to'langan);
    # False — sotuv yaratilish paytidagi to'lov. Statistikada "oxirgi qarz
    # to'lovlari" ro'yxati shu belgi bo'yicha ajratiladi.
    is_debt_payment = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            # Hisobotlar: sana oralig'i + type bo'yicha guruhlash
            models.Index(fields=["created_at"]),
            models.Index(fields=["type", "created_at"]),
        ]

    def clean(self):
        """Model darajasidagi invariant: karta to'lovi kartasiz bo'lmaydi va aksincha."""
        if self.type == self.Type.CARD and self.bank_card_id is None:
            raise ValidationError({"bank_card": "Karta to'lovi uchun bank_card majburiy"})
        if self.type == self.Type.CASH and self.bank_card_id is not None:
            raise ValidationError({"bank_card": "Naqd to'lovda bank_card bo'sh bo'lishi kerak"})

    def save(self, *args, **kwargs):
        # Invariant serializerdan tashqari (admin, shell, service) yozuvlarda ham ishlashi uchun
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sale.store.name} {self.customer.full_name if self.customer else ''} {str(self.amount)}"



class SaleReturn(TimestampMixin):

    sale = models.ForeignKey(
        Sale,
        on_delete=models.PROTECT,
        related_name="returns"
    )

    store = models.ForeignKey('store.Store', on_delete=models.PROTECT)
    customer = models.ForeignKey(
        'users.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    total_refund = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    # Shu qaytarimda mijozga qaytarilgan pul (Payment, is_refund=True) qatorlarini
    # bog'lovchi guruh — detailda qaysi usul/kartadan qancha qaytarilgani ko'rinadi.
    # NULL — pul qaytarilmagan (hammasi qarzdan yopilgan) yoki eski yozuvlar.
    payment_group = models.UUIDField(null=True, blank=True, db_index=True)

    comment = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'sale_return'

    def __str__(self):
        return f"#{self.sale} Sotuv - {str(self.total_refund)} product"


class SaleReturnItem(models.Model):
    sale_return = models.ForeignKey(
        SaleReturn,
        on_delete=models.CASCADE,
        related_name="items"
    )

    sale_item = models.ForeignKey(
        SaleItem,
        on_delete=models.PROTECT
    )

    product = models.ForeignKey('products.Product', on_delete=models.PROTECT)

    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=20, decimal_places=2)
    total_price = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        db_table = 'sale_return_item'

    def __str__(self):
        return f"#{self.sale_return} Sotuv - {str(self.quantity)} product"
