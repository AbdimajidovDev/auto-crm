from rest_framework import serializers

class InventoryListSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(source="product.id")
    product_name = serializers.CharField(source="product.name")

    declared = serializers.IntegerField(source="expected_quantity")
    scanned = serializers.IntegerField(source="counted")

    sold_out = serializers.IntegerField()
    returned = serializers.IntegerField()
    transfer_out = serializers.IntegerField()
    transfer_in = serializers.IntegerField()
    entry = serializers.IntegerField()

    status = serializers.CharField()
    is_check = serializers.BooleanField()

    final = serializers.SerializerMethodField()
    difference = serializers.SerializerMethodField()

    def get_final(self, obj):
        return (
                obj.counted
                - obj.sold_out
                - obj.transfer_out
                + obj.transfer_in
                + obj.entry
                + obj.returned
        )

    def get_difference(self, obj):
        final = self.get_final(obj)
        return final - obj.expected_quantity


class InventoryStartSerializer(serializers.Serializer):
    store_id = serializers.IntegerField()


class InventoryCountSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=0)


class InventoryFinalizeSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()


class InventoryCancelSerializer(serializers.Serializer):
    session_id = serializers.IntegerField()