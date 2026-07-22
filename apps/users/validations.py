import re
import phonenumbers
from rest_framework.exceptions import ValidationError

from apps.common.i18n import tr


def check_code_validator(obj):
    if not str(obj).isdigit() or len(str(obj)) != 6:
        raise ValidationError({'error': tr("otp_invalid")})


def check_valid_email(email):
    email_regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    if not re.match(email_regex, email):
        raise ValidationError({'error': tr("invalid_email")})
    return True


def check_valid_phone(phone_number):
    try:
        parsed_number = phonenumbers.parse(phone_number)
        if not phonenumbers.is_valid_number(parsed_number):
            raise ValidationError({'error': tr("invalid_phone")})
    except phonenumbers.NumberParseException:
        raise ValidationError({'error': tr("invalid_phone_format")})
    return phone_number
