from rest_framework import serializers
from apps.products.models import BarcodeTemplate
from apps.products.services.label_engine import LabelTemplateValidator, LabelValidationError


class BarcodeTemplateListSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    elements_count = serializers.SerializerMethodField()

    class Meta:
        model = BarcodeTemplate
        fields = (
            "id",
            "name",
            "description",
            "width_mm",
            "height_mm",
            "barcode_format",
            "is_default",
            "is_active",
            "elements_count",
            "created_at",
            "updated_at",
            "created_by_username",
        )
        read_only_fields = ("id", "created_at", "updated_at", "created_by_username")

    def get_elements_count(self, obj) -> int:
        if isinstance(obj.layout, dict):
            elements = obj.layout.get("elements", [])
            return len(elements) if isinstance(elements, list) else 0
        return 0


class BarcodeTemplateDetailSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    warnings = serializers.SerializerMethodField()

    class Meta:
        model = BarcodeTemplate
        fields = (
            "id",
            "name",
            "description",
            "width_mm",
            "height_mm",
            "barcode_format",
            "layout",
            "warnings",
            "is_default",
            "is_active",
            "created_at",
            "updated_at",
            "created_by_username",
        )
        read_only_fields = ("id", "created_at", "updated_at", "created_by_username", "warnings")

    def get_warnings(self, obj) -> list[str]:
        if hasattr(self, "_last_warnings"):
            return self._last_warnings
        # Calculate advisory warnings on read
        if isinstance(obj.layout, dict):
            try:
                _, warnings = LabelTemplateValidator.validate(obj.layout, obj.width_mm, obj.height_mm)
                return warnings
            except Exception:
                return []
        return []

    def validate(self, attrs):
        width_mm = attrs.get("width_mm") or (self.instance.width_mm if self.instance else None)
        height_mm = attrs.get("height_mm") or (self.instance.height_mm if self.instance else None)
        layout = attrs.get("layout") or (self.instance.layout if self.instance else None)

        if not width_mm or not height_mm:
            raise serializers.ValidationError("width_mm va height_mm ko'rsatilishi shart.")

        if not layout:
            raise serializers.ValidationError("layout ko'rsatilishi shart.")

        try:
            cleaned_layout, warnings = LabelTemplateValidator.validate(layout, width_mm, height_mm)
            attrs["layout"] = cleaned_layout
            self._last_warnings = warnings
        except LabelValidationError as e:
            raise serializers.ValidationError({"layout": str(e.message)})

        return attrs


class BarcodeLabelPreviewRequestSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    template_id = serializers.IntegerField(required=False, allow_null=True)
    store_id = serializers.IntegerField(required=False, allow_null=True)
    layout_override = serializers.DictField(required=False, allow_null=True)


class BarcodePrintItemSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(required=True)
    quantity = serializers.IntegerField(required=False, default=1, min_value=1, max_value=1000)


class BarcodeLabelPrintRequestSerializer(serializers.Serializer):
    template_id = serializers.IntegerField(required=False, allow_null=True)
    store_id = serializers.IntegerField(required=False, allow_null=True)
    items = BarcodePrintItemSerializer(many=True, required=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Kamida bitta tovar tanlanishi shart.")
        if len(value) > 500:
            raise serializers.ValidationError("Bir vaqtning o'zida ko'pi bilan 500 ta tovar chop etish mumkin.")
        total_qty = sum(item.get("quantity", 1) for item in value)
        if total_qty > 1000:
            raise serializers.ValidationError("Bir martalik chop etishda jami nusxalar soni 1000 tadan oshmasligi kerak.")
        return value
