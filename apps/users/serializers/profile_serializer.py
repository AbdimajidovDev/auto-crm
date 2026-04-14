from rest_framework import serializers

from apps.users.models import User
from apps.users.serializers import UserHistorySerializer


from rest_framework import serializers
from apps.store.models import Store

class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ("id", "name", "phone_number", "address", "type", "is_active")



class ProfileSerializer(serializers.ModelSerializer):
    history = UserHistorySerializer(many=True, read_only=True)
    stores = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "is_superuser",
            "full_name",
            "phone_number",
            "email",
            "stores",
            "history",
        )

        extra_kwargs = {
            "id": {"read_only": True},
            "is_superuser": {'read_only': True},
            "phone_number": {'read_only': True},
            "email": {'read_only': True},
        }

    def get_stores(self, obj):
        request = self.context.get("request")
        user = request.user

        # 🔐 SUPERUSER → hamma store
        if user.is_superuser:
            stores = Store.objects.filter(is_active=True)

        else:
            # 🔐 faqat o‘ziga tegishli storelar
            stores = Store.objects.filter(
                user_links__user=user,
                user_links__is_active=True,
                is_active=True
            ).distinct()

        return StoreSerializer(stores, many=True).data


#
# class ProfileSerializer(serializers.ModelSerializer):
#     history = UserHistorySerializer(many=True, read_only=True)
#
#     class Meta:
#         model = User
#         fields = ("id", "is_superuser", "full_name", "phone_number", "email", "history")
#
#         extra_kwargs = {
#             "id": {"read_only": True},
#             "is_superuser": {'read_only': True},
#             "phone_number": {'read_only': True},
#             "email": {'read_only': True},
#         }
