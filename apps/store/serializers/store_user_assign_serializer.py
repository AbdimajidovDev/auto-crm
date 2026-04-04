from rest_framework import serializers


class StoreUserAssignSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    store_id = serializers.IntegerField()