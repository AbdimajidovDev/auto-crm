import re
import phonenumbers
from rest_framework.exceptions import ValidationError


def check_code_validator(obj):
    if not str(obj).isdigit() or len(str(obj)) != 6:
        raise ValidationError({'error': "OTP code is invalid"})


def check_valid_email(email):
    email_regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    if not re.match(email_regex, email):
        raise ValidationError({'error': 'Invalid email address'})
    return True


def check_valid_phone(phone_number):
    try:
        parsed_number = phonenumbers.parse(phone_number)
        if not phonenumbers.is_valid_number(parsed_number):
            raise ValidationError({'error': "Yaroqsiz telefon raqam!"})
    except phonenumbers.NumberParseException:
        raise ValidationError({'error': "Telefon raqam formati noto'g'ri! (+_)"})
    return phone_number