from rest_framework import serializers

from apps.users.models import User
from apps.users.serializers import UserHistorySerializer


class ProfileSerializer(serializers.ModelSerializer):
    history = UserHistorySerializer(many=True, read_only=True)
    # role = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "full_name", "phone_number", "email", "history")

        extra_kwargs = {
            "id": {"read_only": True},
            # "role": {'read_only': True},
            "phone_number": {'read_only': True},
            "email": {'read_only': True},
        }
