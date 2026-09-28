from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.common.quantity import QuantityField
from apps.products.models import Product, ProductImage, ProductBatch, ProductLocation, ProductFieldHistory
from apps.products.utils.barcode_utility import normalize_barcode, generate_barcode_image


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ('id', 'image')


# class ProductBatchSerializer(serializers.ModelSerializer):
#     # ⚠️ MUAMMO [PERFORMANCE]: Uchta `SerializerMethodField` FK/nested maydonlarni o'qiydi.
#     # Sabab: querysetda `select_related("store", "product", "location")` bo'lmasa har batch uchun qo'shimcha querylar chiqadi.
#     # Natija: product detail/listda batchlar ko'p bo'lsa N+1 muammosi yuzaga keladi.
#     # ✅ YECHIM:
#     # store_name = serializers.CharField(source="store.name", read_only=True)
#     # product_name = serializers.CharField(source="product.name", read_only=True)
#     # location = ProductLocationGetSerializer(read_only=True)
#     # N+1: list/detailda batchlar soni ko'p bo'lsa `store`, `product`, `location` uchun prefetch kerak.
#     store_name = serializers.SerializerMethodField()
#     product_name = serializers.SerializerMethodField()
#     location = serializers.SerializerMethodField()
#
#     class Meta:
#         model = ProductBatch
#         fields = (
#             'id', 'product', 'product_name', 'store', 'store_name', 'quantity',
#             'purchase_price', 'selling_price', 'barcode', 'shtrix_code', "location"
#         )
#
#     def get_store_name(self, obj):
#         return obj.store.name if obj.store else None
#
#     def get_product_name(self, obj):
#         return obj.product.name if obj.product else None
#
#     def get_location(self, obj):
#         if obj.location:
#             name = obj.location.location
#             description = obj.location.description
#             location = {
#                 "name": name,
#                 "description": description,
#                 }
#             return location
#         return None

class ProductBatchLocationUpdateSerializer(serializers.ModelSerializer):
    # Faqat location ID-sini qabul qilamiz
    location = serializers.PrimaryKeyRelatedField(
        queryset=ProductLocation.objects.all(),
        required=True
    )

    class Meta:
        model = ProductBatch
        fields = ['location']


# class ProductByBarcodeSerializer(serializers.ModelSerializer):
#     product = serializers.SerializerMethodField()
#     price = serializers.SerializerMethodField()
#
#     class Meta:
#         model = ProductBatch
#         fields = ('id', 'product', 'price', 'quantity', "location")
#
#     def get_product(self, obj):
#         return obj.product.name if obj.product else None
#
#     def get_price(self, obj):
#         return obj.selling_price or None

# ─────────────────────────────────────────────
# SERIALIZERS
# ─────────────────────────────────────────────


# class ProductBatchListSerializer(serializers.ModelSerializer):
#     # select_related("store", "location") queryset darajasida hal qilinadi —
#     # bu yerda qo'shimcha query bo'lmaydi.
#     store_name = serializers.CharField(source="store.name", read_only=True)
#     location_name = serializers.CharField(
#         source="location.location", read_only=True, default=None
#     )
#
#     class Meta:
#         model = ProductBatch
#         fields = (
#             "id", "store", "store_name", "location", "location_name",
#             "quantity", "purchase_price", "selling_price",
#             "barcode", "is_active",
#         )



# ============================================================
# SERIALIZERS
# ============================================================
class ProductBatchListSerializer(serializers.Serializer):
    id             = serializers.IntegerField(allow_null=True)
    store          = serializers.IntegerField(source="store_id")
    store_name     = serializers.CharField()
    location       = serializers.IntegerField(allow_null=True)
    location_name  = serializers.CharField(allow_null=True)
    quantity       = QuantityField()
    min_stock      = QuantityField(allow_null=True)
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    selling_price  = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    wholesale_price  = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    is_active      = serializers.BooleanField(allow_null=True)


class ProductListSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    category_name = serializers.CharField(
        source="category.name", read_only=True, default=None
    )
    brand_name = serializers.CharField(
        source="brand.name", read_only=True, default=None
    )
    unit_measurement_name = serializers.CharField(
        source="unit_measurement.measurement", read_only=True, default=None
    )
    purchase_price = serializers.SerializerMethodField()
    selling_price = serializers.SerializerMethodField()
    wholesale_price = serializers.SerializerMethodField()
    # batches endi SerializerMethodField — barcha do'konlarni qamrab oladi
    batches = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "category", "category_name",
            "brand", "brand_name",
            "name",
            "sku", "barcode", "shtrix_code",
            "unit_measurement", "unit_measurement_name",
            "description",
            "min_stock",
            "is_pair",
            "status",
            "created_at",
            "images",
            "purchase_price",
            "selling_price",
            "wholesale_price",
            "batches",
        )

    def _get_target_store_id(self):
        store_id = self.context.get("store_id")
        if store_id is not None:
            return int(store_id) if str(store_id).isdigit() else None
        request = self.context.get("request")
        if request:
            params = getattr(request, "query_params", getattr(request, "GET", {}))
            raw_store = params.get("store_id") or params.get("store")
            if raw_store and str(raw_store).isdigit():
                return int(raw_store)
        return None

    def _get_scoped_batch(self, product):
        store_id = self._get_target_store_id()
        if store_id is None:
            return None

        if not hasattr(product, "_scoped_batch_cache"):
            product._scoped_batch_cache = {}
        elif not isinstance(product._scoped_batch_cache, dict):
            product._scoped_batch_cache = {}

        if store_id in product._scoped_batch_cache:
            return product._scoped_batch_cache[store_id]

        chosen_batch = None
        if hasattr(product, "_prefetched_objects_cache") and "batches" in product._prefetched_objects_cache:
            for b in product.batches.all():
                if b.store_id == store_id:
                    chosen_batch = b
                    break
        else:
            chosen_batch = product.batches.filter(store_id=store_id).first()

        product._scoped_batch_cache[store_id] = chosen_batch
        return chosen_batch

    def _resolve_latest_inbound_prices(self, product):
        """
        Annotatsiya qilingan yoki eng oxirgi kirim (StockEntryItem / StockTransferItem / ProductBatch)
        orqali mahsulotning barcha 3 ta narxini (purchase, selling, wholesale) bitta yagona eventdan oladi.
        """
        store_id = self._get_target_store_id()
        if not hasattr(product, "_cached_latest_inbound_prices") or not isinstance(product._cached_latest_inbound_prices, dict):
            product._cached_latest_inbound_prices = {}

        if store_id in product._cached_latest_inbound_prices:
            return product._cached_latest_inbound_prices[store_id]

        # 1. Annotated qiymatlar mavjud bo'lsa darhol O(1) qaytarish
        ann_purchase = getattr(product, "latest_purchase_price", None)
        ann_selling = getattr(product, "latest_selling_price", None)
        ann_wholesale = getattr(product, "latest_wholesale_price", None)
        if ann_selling is not None or ann_purchase is not None or ann_wholesale is not None:
            res = {
                "purchase_price": ann_purchase,
                "selling_price": ann_selling,
                "wholesale_price": ann_wholesale,
            }
            product._cached_latest_inbound_prices[store_id] = res
            return res

        # 2. Standalone serializer chaqiruvlari uchun (annotatsiya bo'lmaganda) fallback
        from apps.contract.models import StockEntryItem
        from apps.transfer.models import StockTransferItem
        from apps.products.models import ProductBatch

        if store_id is not None:
            sei = (
                StockEntryItem.objects.filter(
                    product_id=product.id,
                    entry__store_id=store_id,
                    selling_price__gt=0,
                )
                .order_by("-entry__created_at", "-id")
                .first()
            )
            sti = (
                StockTransferItem.objects.filter(
                    product_id=product.id,
                    stock_transfer__to_store_id=store_id,
                    stock_transfer__status="a",
                    selling_price__gt=0,
                )
                .order_by("-stock_transfer__approved_at", "-id")
                .first()
            )

            winner = None
            if sei and sti:
                t_time = sti.stock_transfer.approved_at or sti.stock_transfer.created_at
                e_time = sei.entry.created_at
                winner = sti if t_time and e_time and t_time > e_time else sei
            elif sti:
                winner = sti
            elif sei:
                winner = sei

            if winner and winner == sti:
                dest_batch = self._get_scoped_batch(product)
                ws = dest_batch.wholesale_price if dest_batch else None
                res = {
                    "purchase_price": sti.purchase_price,
                    "selling_price": sti.selling_price,
                    "wholesale_price": ws,
                }
            elif winner and winner == sei:
                res = {
                    "purchase_price": sei.purchase_price,
                    "selling_price": sei.selling_price,
                    "wholesale_price": sei.wholesale_price,
                }
            else:
                batch = self._get_scoped_batch(product)
                if batch:
                    res = {
                        "purchase_price": batch.purchase_price,
                        "selling_price": batch.selling_price,
                        "wholesale_price": batch.wholesale_price,
                    }
                else:
                    res = {"purchase_price": None, "selling_price": None, "wholesale_price": None}
        else:
            # All stores: latest global stock-in event
            sei = (
                StockEntryItem.objects.filter(
                    product_id=product.id,
                    selling_price__gt=0,
                )
                .order_by("-entry__created_at", "-id")
                .first()
            )
            sti = (
                StockTransferItem.objects.filter(
                    product_id=product.id,
                    stock_transfer__status="a",
                    selling_price__gt=0,
                )
                .order_by("-stock_transfer__approved_at", "-id")
                .first()
            )

            winner = None
            if sei and sti:
                t_time = sti.stock_transfer.approved_at or sti.stock_transfer.created_at
                e_time = sei.entry.created_at
                winner = sti if t_time and e_time and t_time > e_time else sei
            elif sti:
                winner = sti
            elif sei:
                winner = sei

            if winner and winner == sti:
                dest_batch = ProductBatch.objects.filter(
                    product_id=product.id,
                    store_id=sti.stock_transfer.to_store_id,
                ).first()
                res = {
                    "purchase_price": sti.purchase_price,
                    "selling_price": sti.selling_price,
                    "wholesale_price": dest_batch.wholesale_price if dest_batch else None,
                }
            elif winner and winner == sei:
                res = {
                    "purchase_price": sei.purchase_price,
                    "selling_price": sei.selling_price,
                    "wholesale_price": sei.wholesale_price,
                }
            else:
                b = None
                if hasattr(product, "_prefetched_objects_cache") and "batches" in product._prefetched_objects_cache:
                    for item_b in product.batches.all():
                        if item_b.selling_price and item_b.selling_price > Decimal("0.00"):
                            b = item_b
                            break
                if not b:
                    b = product.batches.filter(selling_price__gt=0).order_by("-updated_at", "-id").first()
                if b:
                    res = {
                        "purchase_price": b.purchase_price,
                        "selling_price": b.selling_price,
                        "wholesale_price": b.wholesale_price,
                    }
                else:
                    res = {"purchase_price": None, "selling_price": None, "wholesale_price": None}

        product._cached_latest_inbound_prices[store_id] = res
        return res

    def get_purchase_price(self, product):
        return self._resolve_latest_inbound_prices(product).get("purchase_price")

    def get_selling_price(self, product):
        return self._resolve_latest_inbound_prices(product).get("selling_price")

    def get_wholesale_price(self, product):
        return self._resolve_latest_inbound_prices(product).get("wholesale_price")

    def get_batches(self, product):
        # Context dan barcha do'konlar olinadi (view da set qilinadi)
        all_stores = self.context.get("all_stores", [])

        # Prefetch dan kelgan batchlarni store_id → batch mapping
        batch_map = {
            batch.store_id: batch
            for batch in product.batches.all()
        }

        result = []
        for store in all_stores:
            batch = batch_map.get(store.id)
            if batch:
                result.append({
                    "id":              batch.id,
                    "store_id":        store.id,
                    "store_name":      store.name,
                    "location":        batch.location_id,
                    "location_name":   getattr(batch.location, "location", None),
                    "quantity":        batch.quantity,
                    "min_stock":       batch.min_stock if batch.min_stock > 0 else product.min_stock,
                    "purchase_price":  batch.purchase_price,
                    "selling_price":   batch.selling_price,
                    "wholesale_price": batch.wholesale_price,
                    "is_active":       batch.is_active,
                })
            else:
                # Batch yo'q — virtual yozuv
                result.append({
                    "id":             None,
                    "store_id":       store.id,
                    "store_name":     store.name,
                    "location":       None,
                    "location_name":  None,
                    "quantity":       0,
                    "min_stock":      product.min_stock,
                    "purchase_price": None,
                    "selling_price":  None,
                    "wholesale_price": None,
                    "is_active":      None,
                })

        return ProductBatchListSerializer(result, many=True).data


class ProductDetailSerializer(ProductListSerializer):
    """
    Bitta mahsulotning TO'LIQ ko'rinishi (detail sahifa va tarix sahifasi uchun).

    ProductListSerializer'ning ustiga qo'shiladi: kirill nomi/tavsifi
    (tahrirlash formasi ularni kutadi), o'lchov birligi ID'si va yangilanish
    vaqti. `batches` — do'konlar kesimidagi qoldiq/narxlar: kontekstdagi
    `all_stores` bo'yicha to'ldiriladi (view uni beradi).
    """

    class Meta(ProductListSerializer.Meta):
        # name_uz_cyrl / description_uz_cyrl — modeltranslation qo'shgan haqiqiy
        # model maydonlari, shuning uchun alohida e'lon qilish shart emas
        fields = ProductListSerializer.Meta.fields + (
            "name_uz_cyrl",
            "description_uz_cyrl",
            "updated_at",
        )
        read_only_fields = fields

    def get_batches(self, product):
        # Kontekstda do'kon ro'yxati bo'lmasa — mahsulotning o'z partiyalari
        # (do'kon nomi bilan). Shunda detail javob list bilan bir xil shaklda
        # bo'ladi va frontend qayta ishlashi o'zgarmaydi.
        if self.context.get("all_stores"):
            return super().get_batches(product)

        rows = [
            {
                "id": batch.id,
                "store_id": batch.store_id,
                "store_name": batch.store.name if batch.store_id else "—",
                "location": batch.location_id,
                "location_name": getattr(batch.location, "location", None),
                "quantity": batch.quantity,
                "min_stock": batch.min_stock if batch.min_stock > 0 else product.min_stock,
                "purchase_price": batch.purchase_price,
                "selling_price": batch.selling_price,
                "wholesale_price": batch.wholesale_price,
                "is_active": batch.is_active,
            }
            for batch in product.batches.all()
            if batch.is_active
        ]
        return ProductBatchListSerializer(rows, many=True).data


class ProductCreateSerializer(serializers.ModelSerializer):
    images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
        default=list
    )

    # Nomi — majburiy, kamida 3 belgi. Xabarlar o'zbekcha: frontend ularni
    # toast va input ostida to'g'ridan-to'g'ri ko'rsatadi.
    name_uz = serializers.CharField(
        max_length=100,
        min_length=3,
        error_messages={
            "required": "Mahsulot nomi kiritilishi shart.",
            "blank": "Mahsulot nomi kiritilishi shart.",
            "null": "Mahsulot nomi kiritilishi shart.",
            "min_length": "Mahsulot nomi kamida 3 belgidan iborat bo'lishi kerak.",
            "max_length": "Mahsulot nomi 100 belgidan oshmasligi kerak.",
        },
    )
    description_uz = serializers.CharField(
        required=False, allow_blank=True, allow_null=True,
    )

    # barcode va sku — ixtiyoriy. Kelsa berilgan qiymat ishlatiladi,
    # kelmasa model save() ichida avtomatik generatsiya qilinadi.
    barcode = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=13
    )
    sku = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=64
    )

    def validate_description_uz(self, value):
        # Tavsif ixtiyoriy, lekin kiritilsa kamida 3 belgi bo'lishi kerak
        if value and len(value.strip()) < 3:
            raise serializers.ValidationError(
                "Tavsif kamida 3 belgidan iborat bo'lishi kerak."
            )
        return value

    class Meta:
        model = Product
        fields = (
            'id',
            'category',
            'brand',
            'unit_measurement',
            'name_uz',
            'name_uz_cyrl',
            'description_uz',
            'description_uz_cyrl',
            'min_stock',
            'is_pair',
            'barcode',
            'sku',
            'images'
        )

    def validate_barcode(self, value):
        # Bo'sh kelsa — None, save() avtomatik generatsiya qiladi
        if not value:
            return None

        # EAN-13 formatga normallashtirish (12-13 raqam, checksum bilan).
        # Bu self.barcode va generatsiya qilingan shtrix rasm mos kelishini kafolatlaydi.
        try:
            full_code = normalize_barcode(value)
        except Exception:
            raise serializers.ValidationError(
                "Barcode yaroqli EAN-13 formatda bo'lishi kerak (12-13 raqam)."
            )

        if Product.objects.filter(barcode=full_code).exists():
            raise serializers.ValidationError("Bu barcode allaqachon mavjud.")

        return full_code

    def validate_sku(self, value):
        # Bo'sh kelsa — None, save() avtomatik generatsiya qiladi
        if not value:
            return None

        value = value.strip()
        if Product.objects.filter(sku=value).exists():
            raise serializers.ValidationError("Bu SKU allaqachon mavjud.")

        return value

    def to_internal_value(self, data):
        """
        UNIVERSAL PARSER:
        images, images[], images[0] hammasini ushlaydi
        """
        files = []

        # DRF QueryDict bo‘lsa
        if hasattr(data, "getlist"):
            files.extend(data.getlist("images"))
            files.extend(data.getlist("images[]"))

        # fallback (images[0], images[1] ...)
        for key in data:
            if key.startswith("images["):
                files.append(data.get(key))

        if files:
            data.setlist("images", files)

        return super().to_internal_value(data)

    def validate_images(self, images):
        MAX_PRODUCT_IMAGES = 7
        MAX_PRODUCT_IMAGE_SIZE = 5 * 1024 * 1024
        if len(images) > MAX_PRODUCT_IMAGES:
            raise serializers.ValidationError("Ko'pi bilan 7 ta rasm yuklash mumkin.")

        for img in images:
            if img.size > MAX_PRODUCT_IMAGE_SIZE:
                raise serializers.ValidationError("Har bir rasm hajmi 5MB dan kichik bo'lishi kerak.")

        return images


class ProductGetSerializer(serializers.ModelSerializer):
    category_name = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "name", "description", "min_stock", "category", "category_name", "created_at"
        )

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None


class ProductUpdateSerializer(serializers.ModelSerializer):

    # Mavjud rasmlar — detail GET javobida frontend tahrirlash formasi
    # ularni ko'rsatishi va o'chirish uchun IDsini bilishi kerak
    images = ProductImageSerializer(many=True, read_only=True)

    # yangi rasmlar
    new_images = serializers.ListField(
        child=serializers.ImageField(),
        required=False,
        write_only=True
    )

    # o‘chiriladigan rasmlar IDsi
    delete_image_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        write_only=True
    )

    # barcode va sku — ixtiyoriy. Kelsa yangilanadi, kelmasa (yoki bo'sh) o'zgarmaydi.
    barcode = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=13
    )
    sku = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=64
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "category",
            "unit_measurement",
            "name",
            "description",
            "min_stock",
            "is_pair",
            "barcode",
            "sku",
            "status",
            "images",
            "new_images",
            "delete_image_ids",
        )

    def validate_new_images(self, images):
        if len(images) > 7:
            raise serializers.ValidationError("Ko'pi bilan 7 ta rasm yuklash mumkin.")

        for img in images:
            if img.size > 5 * 1024 * 1024:
                raise serializers.ValidationError("Har bir rasm hajmi 5MB dan kichik bo'lishi kerak.")

        return images

    def validate_name(self, value):
        # Nomi yangilanayotgan bo'lsa — bo'sh bo'lmasin va kamida 3 belgi bo'lsin
        if value is not None:
            trimmed = value.strip()
            if not trimmed:
                raise serializers.ValidationError("Mahsulot nomi kiritilishi shart.")
            if len(trimmed) < 3:
                raise serializers.ValidationError(
                    "Mahsulot nomi kamida 3 belgidan iborat bo'lishi kerak."
                )
        return value

    def validate_barcode(self, value):
        # Bo'sh kelsa — o'zgartirmaymiz
        if not value:
            return None

        try:
            full_code = normalize_barcode(value)
        except Exception:
            raise serializers.ValidationError(
                "Barcode yaroqli EAN-13 formatda bo'lishi kerak (12-13 raqam)."
            )

        qs = Product.objects.filter(barcode=full_code)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Bu barcode allaqachon mavjud.")

        return full_code

    def validate_sku(self, value):
        # Bo'sh kelsa — o'zgartirmaymiz
        if not value:
            return None

        value = value.strip()
        qs = Product.objects.filter(sku=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Bu SKU allaqachon mavjud.")

        return value

    @transaction.atomic
    def update(self, instance, validated_data):
        new_images = validated_data.pop("new_images", [])
        delete_ids = validated_data.pop("delete_image_ids", [])
        barcode_value = validated_data.pop("barcode", None)
        sku_value = validated_data.pop("sku", None)

        request = self.context.get("request")
        user = getattr(request, "user", None)
        user_display = (
            getattr(user, "full_name", "")
            or getattr(user, "phone_number", "")
            or "Tizim"
        ) if user and user.is_authenticated else "Tizim"

        # Old snapshot
        old_snapshot = {
            "name": instance.name,
            "name_cyrl": getattr(instance, "name_cyrl", ""),
            "description": instance.description,
            "category": str(instance.category.name) if instance.category else "",
            "brand": str(instance.brand.name) if instance.brand else "",
            "unit_measurement": str(instance.unit_measurement.measurement) if instance.unit_measurement else "",
            "sku": instance.sku or "",
            "barcode": instance.barcode or "",
            "min_stock": str(instance.min_stock),
            "status": instance.get_status_display() if hasattr(instance, "get_status_display") else instance.status,
            "is_pair": "Juft (0.25)" if instance.is_pair_effective else "Dona (1)",
        }

        # product fields update
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # barcode qo'lda o'zgartirilsa — yangilanadi va mos shtrix rasm qayta yaratiladi
        if barcode_value and barcode_value != instance.barcode:
            instance.barcode = barcode_value
            instance.shtrix_code.save(
                f"{barcode_value}.png",
                generate_barcode_image(barcode_value),
                save=False,
            )

        # sku qo'lda o'zgartirilsa — yangilanadi
        if sku_value:
            instance.sku = sku_value

        instance.save()

        # Check differences and record in ProductFieldHistory
        field_labels = {
            "name": "Mahsulot nomi",
            "name_cyrl": "Mahsulot nomi (Kirill)",
            "description": "Tavsif",
            "category": "Kategoriya",
            "brand": "Brend",
            "unit_measurement": "O‘lchov birligi",
            "sku": "SKU",
            "barcode": "Shtrixkod",
            "min_stock": "Minimal qoldiq",
            "status": "Holati (Faollik)",
            "is_pair": "Dona / Juft",
        }

        new_snapshot = {
            "name": instance.name,
            "name_cyrl": getattr(instance, "name_cyrl", ""),
            "description": instance.description,
            "category": str(instance.category.name) if instance.category else "",
            "brand": str(instance.brand.name) if instance.brand else "",
            "unit_measurement": str(instance.unit_measurement.measurement) if instance.unit_measurement else "",
            "sku": instance.sku or "",
            "barcode": instance.barcode or "",
            "min_stock": str(instance.min_stock),
            "status": instance.get_status_display() if hasattr(instance, "get_status_display") else instance.status,
            "is_pair": "Juft (0.25)" if instance.is_pair_effective else "Dona (1)",
        }

        field_history_entries = []
        for field, old_val in old_snapshot.items():
            new_val = new_snapshot.get(field, "")
            if str(old_val or "").strip() != str(new_val or "").strip():
                field_history_entries.append(
                    ProductFieldHistory(
                        product=instance,
                        field_name=field,
                        field_label=field_labels.get(field, field),
                        old_value=str(old_val or ""),
                        new_value=str(new_val or ""),
                        user=user if user and user.is_authenticated else None,
                        user_display=user_display,
                    )
                )

        if field_history_entries:
            ProductFieldHistory.objects.bulk_create(field_history_entries)

        # DELETE IMAGES
        images_to_delete = list(
            ProductImage.objects.filter(id__in=delete_ids, product=instance)
        )

        ProductImage.objects.filter(
            id__in=[img.id for img in images_to_delete]
        ).delete()

        # Fayllar DB tranzaksiyasi muvaffaqiyatli yakunlangandan keyingina
        # o'chiriladi — rollback bo'lsa fayl yo'qolib qolmasligi kerak
        def _delete_files(images=images_to_delete):
            for img in images:
                if img.image:
                    img.image.delete(save=False)

        transaction.on_commit(_delete_files)

        # 🔥 ADD NEW IMAGES
        ProductImage.objects.bulk_create([
            ProductImage(product=instance, image=img)
            for img in new_images
        ])

        return instance


class StoreStockUpdateItemSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()
    new_quantity = QuantityField(
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
        error_messages={"min_value": "Miqdor 0 yoki undan katta bo'lishi kerak"},
    )
    min_stock = QuantityField(
        required=False,
        allow_null=True,
        min_value=Decimal("0"),
        error_messages={"min_value": "MinStock 0 yoki undan katta bo'lishi kerak"},
    )


class ProductUpdateStocksSerializer(serializers.Serializer):
    stores = serializers.ListField(
        child=StoreStockUpdateItemSerializer(),
        allow_empty=False
    )



# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ProductUpdateSerializer.update ni transaction.atomic bilan himoyalash]
# ═══════════════════════════════
