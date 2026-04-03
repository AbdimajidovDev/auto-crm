import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from django.contrib.auth import authenticate
from apps.users.models.misc import UserHistory
from apps.users.validations import check_valid_phone


class AdminLoginSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    def validate(self, attrs):
        phone_number = attrs.get('phone_number')
        password = attrs.get('password')

        check_valid_phone(phone_number)


        user = authenticate(username=phone_number, password=password)  # Backend emailni ham tekshiradi
        print('user', user)

        if user is None:
            raise ValidationError({'message':"phone_number yoki parol noto'g'ri!"})

        if not user.is_active:
            raise ValidationError({'message': 'Foydalanuvchi faol emas!'})

        attrs['user'] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, attrs):
        self.refresh_token = attrs["refresh"]
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError("Passwords do not match.")
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        old_password = data.get('old_password')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')

        if new_password != confirm_password:
            raise ValidationError("Yangi parolingiz tasdiqlash parolingiz bilan bir xil bo'lishi kerak!")

        if len(new_password) < 8:
            raise ValidationError("Parol kamida 8 ta belgidan iborat bo‘lishi kerak.")

        if not re.search(r'[A-Za-z]', new_password):
            raise ValidationError("Parolda kamida bitta harf bo‘lishi kerak.")

        if not re.search(r'\d', new_password):
            raise ValidationError("Parolda kamida bitta raqam bo‘lishi kerak.")

        if old_password == new_password:
            raise ValidationError("Yangi parol eski parolga teng bo'la olmaydi!")

        return data


class UserHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = UserHistory
        fields = ("id", "action", "ip_address", "user_agent", "created_at")
        read_only_fields = fields