from rest_framework import serializers

from apps.users.models.customers import Customer


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = (
            'id', 'full_name', 'phone_number', 'created_at', 'updated_at',
        )
