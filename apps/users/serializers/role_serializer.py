from rest_framework import serializers

from apps.users.models import Role
from apps.users.permissions import ALL_PERMISSION_CODES


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=64),
        allow_empty=True,
        required=False,
        default=list,
    )
    users_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Role
        fields = (
            "id", "name", "description", "permissions", "users_count",
            "created_at", "updated_at",
        )

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Rol nomi bo'sh bo'lishi mumkin emas.")
        qs = Role.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Bu nomdagi rol allaqachon mavjud.")
        return value

    def validate_permissions(self, value):
        # Dublikatlarni olib tashlaymiz, katalogda yo'q kodlarni rad etamiz
        unique = list(dict.fromkeys(value))
        unknown = [code for code in unique if code not in ALL_PERMISSION_CODES]
        if unknown:
            raise serializers.ValidationError(
                f"Noma'lum permission kodlari: {', '.join(unknown)}"
            )
        return unique
