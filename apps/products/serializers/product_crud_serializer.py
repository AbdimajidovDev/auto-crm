from rest_framework import serializers

from apps.products.models import Product, ProductImage, ProductBatch, ProductLocation


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
    quantity       = serializers.IntegerField()
    purchase_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    selling_price  = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
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
            "status",
            "created_at",
            "images",
            "batches",
        )

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
                    "purchase_price": None,
                    "selling_price":  None,
                    "wholesale_price": None,
                    "is_active":      None,
                })

        return ProductBatchListSerializer(result, many=True).data


class ProductCreateSerializer(serializers.ModelSerializer):
    images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=False,
        default=list
    )

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
            'images'
        )

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
            raise serializers.ValidationError("Max 7 images allowed")

        for img in images:
            if img.size > MAX_PRODUCT_IMAGE_SIZE:
                raise serializers.ValidationError("Image size must be < 5MB")

        return images


class ProductGetSerializer(serializers.ModelSerializer):
    category_name = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "name", "description", "category", "category_name", "created_at"
        )

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None


class ProductUpdateSerializer(serializers.ModelSerializer):

    # yangi rasmlar
    new_images = serializers.ListField(
        child=serializers.ImageField(),
        required=False
    )

    # o‘chiriladigan rasmlar IDsi
    delete_image_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False
    )

    class Meta:
        model = Product
        fields = (
            "category",
            "unit_measurement",
            "name",
            "description",
            "new_images",
            "delete_image_ids",
        )

    def validate_new_images(self, images):
        if len(images) > 7:
            raise serializers.ValidationError("Max 7 images allowed")

        for img in images:
            if img.size > 5 * 1024 * 1024:
                raise serializers.ValidationError("Image size must be < 5MB")

        return images

    def update(self, instance, validated_data):
        # ⚠️ MUAMMO [KRITIK]: Product update ichida bir nechta write operatsiya transaction bilan o'ralmagan.
        # Sabab: product save, image file delete, image rows delete va bulk_create alohida bajariladi.
        # Natija: xatolik bo'lsa product o'zgarib, rasm holati yarimta qolishi mumkin.
        # ✅ YECHIM:
        # @transaction.atomic
        # def update(...):
        #     instance.save()
        #     images_to_delete.delete()
        #     ProductImage.objects.bulk_create(...)
        new_images = validated_data.pop("new_images", [])
        delete_ids = validated_data.pop("delete_image_ids", [])

        # product fields update
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()

        # DELETE IMAGES
        images_to_delete = ProductImage.objects.filter(
            id__in=delete_ids,
            product=instance
        )

        for img in images_to_delete:
            if img.image:
                img.image.delete(save=False)

        images_to_delete.delete()

        # 🔥 ADD NEW IMAGES
        ProductImage.objects.bulk_create([
            ProductImage(product=instance, image=img)
            for img in new_images
        ])

        return instance


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 1
# Performance muammolari: 1
# Arxitektura muammolari: 0
# Umumiy baho: 6 / 10
# Prioritet bo'yicha birinchi hal qilinishi kerak: [ProductUpdateSerializer.update ni transaction.atomic bilan himoyalash]
# ═══════════════════════════════
