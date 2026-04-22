from rest_framework import serializers

class InventoryListSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(source="product.id")
    product_name = serializers.CharField(source="product.name")
    declared = serializers.IntegerField(source="expected_quantity")
    scanned = serializers.IntegerField(source="counted")
    moved = serializers.IntegerField()
    status = serializers.CharField()
    final = serializers.SerializerMethodField()
    difference = serializers.SerializerMethodField()
    is_check = serializers.BooleanField()

    def get_final(self, obj):
        return obj.counted - obj.moved

    def get_difference(self, obj):
        return (obj.counted - obj.moved) - obj.expected_quantity



#
# class InventoryListSerializer(serializers.Serializer):
#
#     product_id = serializers.IntegerField(source="product.id")
#     product_name = serializers.CharField(source="product.name")
#
#     declared = serializers.IntegerField(source="expected_quantity")
#     scanned = serializers.IntegerField(source="counted")
#
#     moved = serializers.IntegerField()
#
#     final = serializers.SerializerMethodField()
#     difference = serializers.SerializerMethodField()
#
#     def get_final(self, obj):
#         return obj.counted - obj.moved
#
#     def get_difference(self, obj):
#         return (obj.counted - obj.moved) - obj.expected_quantity



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