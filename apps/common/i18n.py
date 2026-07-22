# Foydalanuvchi ko'radigan xato xabarlarining tarjima katalogi.
# Til LocaleMiddleware tomonidan Accept-Language headeridan aniqlanadi
# (frontend 'uz' yoki 'uz-Cyrl' yuboradi, LANGUAGES: uz / uz-cyrl).
# Class-darajadagi deklaratsiyalarda (serializer error_messages, validator message)
# tr() so'rov kontekstidan tashqarida chaqirilmasligi uchun tr_lazy ishlatiladi.
from django.utils.functional import lazy
from django.utils.translation import get_language

MESSAGES = {
    "passwords_mismatch": {
        "uz": "Parollar mos kelmadi",
        "uz-cyrl": "Пароллар мос келмади",
    },
    "email_exists": {
        "uz": "Bu email allaqachon ro'yxatdan o'tgan",
        "uz-cyrl": "Бу email аллақачон рўйхатдан ўтган",
    },
    "phone_exists": {
        "uz": "Bu telefon raqam bilan foydalanuvchi allaqachon mavjud",
        "uz-cyrl": "Бу телефон рақам билан фойдаланувчи аллақачон мавжуд",
    },
    "invalid_phone": {
        "uz": "Yaroqsiz telefon raqam!",
        "uz-cyrl": "Яроқсиз телефон рақам!",
    },
    "invalid_phone_format": {
        "uz": "Telefon raqam formati noto'g'ri! (+998...)",
        "uz-cyrl": "Телефон рақам формати нотўғри! (+998...)",
    },
    "invalid_email": {
        "uz": "Email manzili noto'g'ri",
        "uz-cyrl": "Email манзили нотўғри",
    },
    "otp_invalid": {
        "uz": "Tasdiqlash kodi noto'g'ri",
        "uz-cyrl": "Тасдиқлаш коди нотўғри",
    },
    "store_not_found": {
        "uz": "Do'kon topilmadi",
        "uz-cyrl": "Дўкон топилмади",
    },
    "role_not_found": {
        "uz": "Bunday rol topilmadi",
        "uz-cyrl": "Бундай рол топилмади",
    },
    "user_not_found": {
        "uz": "Foydalanuvchi topilmadi",
        "uz-cyrl": "Фойдаланувчи топилмади",
    },
    "no_permission_user_create": {
        "uz": "Foydalanuvchi yaratish uchun ruxsat yo'q",
        "uz-cyrl": "Фойдаланувчи яратиш учун рухсат йўқ",
    },
    "field_required": {
        "uz": "Bu maydon to'ldirilishi shart",
        "uz-cyrl": "Бу майдон тўлдирилиши шарт",
    },
    "only_superuser_sales_delete": {
        "uz": "Sotuvlarni faqat superadmin o'chira oladi",
        "uz-cyrl": "Сотувларни фақат суперадмин ўчира олади",
    },
    "no_sales_selected": {
        "uz": "Hech qanday sotuv tanlanmagan",
        "uz-cyrl": "Ҳеч қандай сотув танланмаган",
    },
    # Django parol validatorlari kodlari bilan bir xil nomlangan
    # (translate_password_errors shu kod bo'yicha qidiradi)
    "password_too_short": {
        "uz": "Parol juda qisqa — kamida 8 ta belgidan iborat bo'lishi kerak",
        "uz-cyrl": "Парол жуда қисқа — камида 8 та белгидан иборат бўлиши керак",
    },
    "password_too_common": {
        "uz": "Parol juda oddiy — boshqa parol tanlang",
        "uz-cyrl": "Парол жуда оддий — бошқа парол танланг",
    },
    "password_entirely_numeric": {
        "uz": "Parol faqat raqamlardan iborat bo'lmasligi kerak",
        "uz-cyrl": "Парол фақат рақамлардан иборат бўлмаслиги керак",
    },
    "password_too_similar": {
        "uz": "Parol shaxsiy ma'lumotlaringizga juda o'xshash",
        "uz-cyrl": "Парол шахсий маълумотларингизга жуда ўхшаш",
    },
}


def tr(code: str) -> str:
    entry = MESSAGES.get(code)
    if not entry:
        return code
    lang = (get_language() or "uz").lower()
    return entry.get(lang) or entry["uz"]


tr_lazy = lazy(tr, str)


def translate_password_errors(exc) -> list:
    # DjangoValidationError (validate_password'dan) ichidagi har bir xatoni
    # validator kodi bo'yicha tarjima qiladi; katalogda yo'q kod bo'lsa
    # asl xabar qaytariladi.
    messages = []
    for err in getattr(exc, "error_list", []):
        code = getattr(err, "code", None)
        if code and code in MESSAGES:
            messages.append(tr(code))
        else:
            messages.extend(err.messages)
    return messages or list(getattr(exc, "messages", []))
