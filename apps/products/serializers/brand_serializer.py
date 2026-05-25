from rest_framework import serializers

from apps.products.models import Brand


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = (
            "id",
            "name",
        )
        read_only_fields = ("id",)

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                "Brand name bo'sh bo'lishi mumkin emas."
            )

        qs = Brand.objects.filter(
            name__iexact=value
        )

        if self.instance:
            qs = qs.exclude(
                id=self.instance.id
            )

        if qs.exists():
            raise serializers.ValidationError(
                "Bunday brand mavjud."
            )

        return value