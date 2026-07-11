from rest_framework import serializers

from apps.sales.models import BankCard


class BankCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankCard
        fields = ("id", "name", "is_default", "is_active", "created_at")
        read_only_fields = ("created_at",)
