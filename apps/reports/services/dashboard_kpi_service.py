# AutoCRM loyihasi uchun Hisobot va statistikalar ni chiqarishimiz kerak. Hozir dashboard qismidagi hisobotni chiqaramiz uning json ko'rinishi quidagicha bo'ladi:
#
# {
#   "dashboard": {
#     "filters": {
#       "selected": "monthly",
#       "available": ["daily", "weekly", "monthly", "yearly"],
#       "dateRange": {
#         "from": "2026-04-01",
#         "to": "2026-04-04"
#       }
#     },
#
#     "reports": {
#       "totalProducts": {
#         "label": "Jami mahsulotlar soni",
#         "value": 420,
#       },
#       "totalRevenue": {
#         "label": "Jami daromad",
#         "value": 18500000,
#       },
#       {
#         "totalPaid": {
#         "label": "o'zing yaxshiroq nom ber",
#         "value": 18500000,
#       },
#
#       "totalDebt": {
#         "label": "Jami qarzdorlik",
#         "value": 2300000,
#       },
#
#       "supplierDebt": {
#         "label": "Taminotchidan qarzdorlik",
#         "value": 1200000,
#       }
#     },
#
#     "charts": {
#       "turnover": {
#         "label": "Aylanma summa",
#         "labels": ["Week 1", "Week 2", "Week 3", "Week 4"],
#         "data": [4200000, 5100000, 4600000, 4600000]
#       },
#
#       "totalDebtTrend": {
#         "label": "Mijozlar qarzdorlik dinamikasi",
#         "labels": ["Week 1", "Week 2", "Week 3", "Week 4"],
#         "data": [90000, 110000, 95000, 125000]
#       }
#     },
#   }
# }
#
# agar super user panelidan kirganda barcha do'konlar bo'yicha hisobot chiqishi kerak aks holda o'ziga tegishli do'kon statistikalari chiqishi kerak.
# bu hisobotni haftalik, oylik, yillik filter qila olishi kerak chart ham shunga mos o'zgarishi kerak
# yani:
# haftalik tanlaganda
#         "labels": ["day 1", "day 2 ", "day 3", ... , "day 7"],
#         "data": [900000, 1100000, 950000, ... , 1250000]
# oylik tanlaganda:
#
#         "labels": ["Week 1", "Week 2", "Week 3", "Week 4"],
#         "data": [900000, 1100000, 950000, 1250000]
# yillik tanlaganda:
#
#         "labels": ["month 1", "Month 2", "Month 3", ...,  "Month 12"],
#         "data": [900000, 1100000, 950000, ... , 1250000]
#
# keyin filterda from date to date gacha filter qila olishi kerak.
# Meni tushundingmi. manashu qismni to'liq 0dan oxirigacha production darajada yozib ber.
#
#
# madellar:
#
# class Sale(models.Model):
#     class Status(models.TextChoices):
#         PAID = "paid", "Paid"
#         PARTIAL = "partial", "Partial"
#         DEBT = "debt", "Debt"
#     class DiscountType(models.TextChoices):
#         PERCENTAGE = "p", "Percentage (%)"
#         FIXED = "f", "Fixed Amount"
#     store = models.ForeignKey('store.Store', on_delete=models.CASCADE, db_index=True)
#     customer = models.ForeignKey(
#         'users.Customer',
#         on_delete=models.SET_NULL,
#         null=True,
#         blank=True,
#         db_index=True
#     )
#     seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
#     total_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
#     paid_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
#     status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
#     discount_type = models.CharField(
#         max_length=10,
#         choices=DiscountType.choices,
#         null=True,
#         blank=True
#     )
#     discount_value = models.DecimalField(
#         max_digits=20,
#         decimal_places=2,
#         default=0
#     )
#     discount_amount = models.DecimalField(
#         max_digits=20,
#         decimal_places=2,
#         default=0
#     )
#     created_at = models.DateTimeField(auto_now_add=True)
#     def __str__(self):
#         return f"{self.store.name} {str(self.status)}"
#
# class SaleItem(models.Model):
#     sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
#     product = models.ForeignKey('products.Product', on_delete=models.CASCADE)
#     quantity = models.PositiveIntegerField()
#     unit_price = models.DecimalField(max_digits=20, decimal_places=2)
#     total_price = models.DecimalField(max_digits=20, decimal_places=2)
#
# class Payment(TimestampMixin):
#     class Type(models.TextChoices):
#         CASH = "cash", "Cash"
#         CARD = "card", "Card"
#     sale = models.ForeignKey(
#         "sales.Sale",
#         on_delete=models.CASCADE,
#         null=True,
#         blank=True,
#         related_name="payments"
#     )
#     customer = models.ForeignKey(
#         "users.Customer",
#         on_delete=models.CASCADE,
#         null=True,
#         blank=True,
#         related_name="payments"
#     )
#     amount = models.DecimalField(max_digits=20, decimal_places=2)
#     type = models.CharField(max_length=5, choices=Type.choices)
#     def __str__(self):
#         return f"{self.sale.store.name} {self.customer.full_name} {str(self.amount)}"
#
# class Store(TimestampMixin):
#     class StoreType(models.TextChoices):
#         BASE = "b", "Base"
#         STORE = "s", "Store"
#     name = models.CharField(max_length=255)
#     phone_number = models.CharField(max_length=20, db_index=True)
#     address = models.TextField()
#     type = models.CharField(max_length=10, choices=StoreType.choices)
#     latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
#     longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
#     is_active = models.BooleanField(default=True)
#     class Meta:
#         db_table = "store"
#         verbose_name = "Store"
#         verbose_name_plural = "Stores"
#         indexes = [
#             models.Index(fields=["type"]),
#             models.Index(fields=["is_active"]),
#         ]
#     def __str__(self):
#         return self.name
#
# class StoreUser(TimestampMixin):
#     class Role(models.TextChoices):
#         Manager = "m", "Manager"
#         SELLER = "s", "Seller"
#     user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="store_links")
#     store = models.ForeignKey("store.Store", on_delete=models.CASCADE, related_name="user_links")
#     role = models.CharField(max_length=20, choices=Role.choices)
#     is_active = models.BooleanField(default=True)
#     class Meta:
#         db_table = "store_user"
#         # verbose_name = "Store User"
#         unique_together = ("user", "store")
#         indexes = [
#             models.Index(fields=["user"]),
#             models.Index(fields=["store"]),
#
# class CustomerDebt(TimestampMixin):
#     class Type(models.TextChoices):
#         INCREASE = "i", "Increase"
#         DECREASE = "d", "Decrease"
#     customer = models.ForeignKey(
#         'users.Customer',
#         on_delete=models.CASCADE,
#         related_name='debts'
#     )
#     sale = models.ForeignKey(
#         'sales.Sale',
#         on_delete=models.SET_NULL,
#         null=True,
#         blank=True,
#         related_name='debt_records'
#     )
#     amount = models.DecimalField(max_digits=20, decimal_places=2)
#     type = models.CharField(max_length=2, choices=Type.choices)
#     def __str__(self):
#         return f"{self.customer} | {self.amount} | {self.type}"
#
# class Category(TimestampMixin):
#     slug = models.SlugField(max_length=100, unique=True, blank=True)
#     name = models.CharField(max_length=100)
#     description = models.TextField()
#     image = models.ImageField(upload_to='categories/', blank=True)
#     def save(self, *args, **kwargs):
#         self.slug = slugify(self.name)
#         super().save(*args, **kwargs)
#     def __str__(self):
#         return self.name
#     class Meta:
#         db_table = 'category'
#         # ordering = ['name']
#         verbose_name_plural = 'Categories'
#
# class Product(TimestampMixin):
#     category = models.ForeignKey(Category, on_delete=models.PROTECT)
#     name = models.CharField(max_length=100)
#     description = models.TextField()
#     is_active = models.BooleanField(default=True)
#     def __str__(self):
#         return self.name
#     class Meta:
#         db_table = 'product'
#         # ordering = ['name']
# class ProductImage(models.Model):
#     product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
#     image = models.ImageField(upload_to=product_image_path)
#     def __str__(self):
#         return self.product.name
#     class Meta:
#         db_table = 'product_image'
#
# class ProductBatch(TimestampMixin):
#     product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='batches')
#     store = models.ForeignKey(Store, on_delete=models.CASCADE)
#     quantity = models.IntegerField()
#     purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
#     selling_price = models.DecimalField(max_digits=12, decimal_places=2)
#     barcode = models.CharField(max_length=13, db_index=True, unique=True)
#     shtrix_code = models.ImageField(upload_to=product_barcode_path)
#     is_active = models.BooleanField(default=True)
#     class Meta:
#         db_table = "product_batch"
#         unique_together = ("store", "barcode")
#         indexes = [
#             models.Index(fields=["store", "product"]),
#         ]
#
# class Customer(TimestampMixin):
#     full_name = models.CharField(max_length=100)
#     phone_number = models.CharField(max_length=20)
#
#     def __str__(self):
#         return self.full_name
#
# class User(AbstractBaseUser, PermissionsMixin, TimestampMixin):
#     full_name = models.CharField(max_length=128, null=True)
#     phone_number = models.CharField(max_length=20, unique=True, db_index=True)
#     email = models.EmailField(unique=True, db_index=True, blank=True, null=True)
#     is_active = models.BooleanField(default=True)
#     is_staff = models.BooleanField(default=False)  # admin panel
#     objects = UserManager()
#     USERNAME_FIELD = "phone_number"
#     REQUIRED_FIELDS = []
#     def clean(self):
#         super().clean()
#         errors = {}
#         if self.is_superuser and not self.email:
#             if not self.email:
#                 errors["email"] = "Superuser uchun email majburiy."
#         if errors:
#             raise ValidationError(errors)
#     def __str__(self):
#         return f"{self.full_name} - ({self.phone_number})"
#     class Meta:
#         # db_table = "user"
#         verbose_name = "User"
#         verbose_name_plural = "Users"
#
#     def get_tokens(self):
#         refresh = RefreshToken.for_user(self)
#         return {
#             "refresh": str(refresh),
#             "access": str(refresh.access_token),
#         }
#
# class Supplier(TimestampMixin):
#     name = models.CharField(max_length=255)
#     phone_number = models.CharField(max_length=20, db_index=True)
#     description = models.TextField()
#     inn = models.CharField(max_length=50, unique=True)
#     address = models.TextField()
#     is_active = models.BooleanField(default=True)
#     class Meta:
#         db_table = "supplier"
#         verbose_name = "Supplier"
#         verbose_name_plural = "Suppliers"
#         indexes = [
#             models.Index(fields=["phone_number"]),
#             models.Index(fields=["inn"]),
#         ]
#     def __str__(self):
#         return self.name
#     def get_total_debt(self):
#         # Jami kirim qilingan qarzlar yig'indisi
#         total_in = self.transactions.filter(
#             type=SupplierTransaction.TransactionType.INVENTORY_IN
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
#         # Jami to'langan summalar yig'indisi
#         total_paid = self.transactions.filter(
#             type=SupplierTransaction.TransactionType.PAYMENT
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
#
#         return total_in - total_paid
#
# class StockEntry(TimestampMixin):
#     supplier = models.ForeignKey(
#         "contract.Supplier",
#         on_delete=models.PROTECT,
#         related_name="entries"
#     )
#     store = models.ForeignKey(
#         "store.Store",
#         on_delete=models.PROTECT,
#         related_name="entries"
#     )
#     total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
#     paid_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
#     created_by = models.ForeignKey(
#         "users.User",
#         on_delete=models.SET_NULL,
#         null=True
#     )
#     class Meta:
#         db_table = "stock_entry"
#         ordering = ["-created_at"]
#     def __str__(self):
#         return f"#{self.pk}. {self.supplier} - {self.store}"
#
# class StockEntryItem(models.Model):
#     entry = models.ForeignKey(
#         StockEntry,
#         on_delete=models.CASCADE,
#         related_name="items"
#     )
#     product = models.ForeignKey(
#         "products.Product",
#         on_delete=models.PROTECT
#     )
#     quantity = models.PositiveIntegerField()
#     purchase_price = models.DecimalField(max_digits=12, decimal_places=2)
#     selling_price = models.DecimalField(max_digits=12, decimal_places=2)
#     class Meta:
#         db_table = "stock_entry_item"
#     def __str__(self):
#         return f"{self.entry.supplier.name} - {self.product.name}"
#
# class SupplierTransaction(TimestampMixin):
#     class TransactionType(models.TextChoices):
#         INVENTORY_IN = "in", "Inventory Intake (Debt Increase)"
#         PAYMENT = "pay", "Payment to Supplier (Debt Decrease)"
#     supplier = models.ForeignKey("contract.Supplier", on_delete=models.CASCADE, related_name="transactions")
#     entry = models.ForeignKey(StockEntry, on_delete=models.SET_NULL, null=True, blank=True)
#     amount = models.DecimalField(max_digits=15, decimal_places=2)
#     type = models.CharField(max_length=5, choices=TransactionType.choices)
#     note = models.TextField(blank=True)
#
#     class Meta:
#         db_table = "supplier_transaction"
#
# yana birorta madelim kerak bo'lsa ayt tashlab beraman.
