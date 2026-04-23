from rest_framework import serializers

from apps.products.models import Product, ProductImage, ProductBatch, ProductLocation


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ('image', 'product')


class ProductBatchSerializer(serializers.ModelSerializer):
    store_name = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()

    class Meta:
        model = ProductBatch
        fields = (
            'id', 'product', 'product_name', 'store', 'store_name', 'quantity',
            'purchase_price', 'selling_price', 'barcode', 'shtrix_code', "location"
        )

    def get_store_name(self, obj):
        return obj.store.name if obj.store else None

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_location(self, obj):
        if obj.location:
            name = obj.location.location
            description = obj.location.description
            location = {
                "name": name,
                "description": description,
                }
            return location
        return None

class ProductBatchLocationUpdateSerializer(serializers.ModelSerializer):
    # Faqat location ID-sini qabul qilamiz
    location = serializers.PrimaryKeyRelatedField(
        queryset=ProductLocation.objects.all(),
        required=True
    )

    class Meta:
        model = ProductBatch
        fields = ['location']


class ProductByBarcodeSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    class Meta:
        model = ProductBatch
        fields = ('id', 'product', 'price', 'quantity', "location")

    def get_product(self, obj):
        return obj.product.name if obj.product else None

    def get_price(self, obj):
        return obj.selling_price or None


class ProductListSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    category_name = serializers.SerializerMethodField()
    batches = ProductBatchSerializer(many=True, read_only=True)
    unit_measurement_name = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'category', 'category_name', 'name', "unit_measurement", 'unit_measurement_name',
            'description', 'is_active', 'created_at', 'images', 'batches'
        )

    def get_category_name(self, obj):
        return obj.category.name if obj.category else None

    def get_unit_measurement_name(self, obj):
        return obj.unit_measurement.measurement if obj.unit_measurement else None


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
        if len(images) > 7:
            raise serializers.ValidationError("Max 7 images allowed")

        for img in images:
            if img.size > 5 * 1024 * 1024:
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

