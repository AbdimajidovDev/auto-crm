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

        # ✅ YAXSHI: `name` ustunida `db_index=True` bor (Brand modeli), shu sabab bu `iexact` mavjudlik
        # tekshiruvi indeksdan foydalanadi — validatsiya arzon. Faqat write (POST/PUT) yo'lida ishlaydi.
        if qs.exists():
            raise serializers.ValidationError(
                "Bunday brand mavjud."
            )

        return value


# ═══════════════════════════════
# 📊 FAYL XULOSASI
# Kritik muammolar soni: 0
# Performance muammolari: 0
# Arxitektura muammolari: 0
# Umumiy baho: 9 / 10
# Izoh: Oddiy ModelSerializer, 2 ta maydon, GET tomonida N+1 yo'q. Validatsiya faqat write yo'lida.
# Prioritet bo'yicha birinchi hal qilinishi kerak: [—]
# ═══════════════════════════════