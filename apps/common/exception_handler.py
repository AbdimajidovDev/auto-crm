"""
Global DRF exception handler.

Maqsad — ma'lumotlar bazasi darajasidagi tipik xatolar foydalanuvchiga 500
o'rniga tushunarli javob bilan qaytsin:

  * ProtectedError    — moliyaviy yozuvga bog'langan obyektni o'chirishga urinish (409);
  * ObjectDoesNotExist — `.get()` bilan topilmagan obyekt (404);
  * IntegrityError    — unique/constraint buzilishi (409).

Bularsiz `Model.objects.get(...)` yoki `instance.delete()` chaqirgan har bir
servis xatoni o'zi ushlashi kerak edi; amalda ko'p joyda ushlanmay 500 qaytardi.
"""

import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import ProtectedError, RestrictedError
from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, (ProtectedError, RestrictedError)):
        logger.warning("ProtectedError: %s", exc, exc_info=True)
        return Response(
            {"detail": "Bu yozuv boshqa ma'lumotlarda ishlatilgan, o'chirib bo'lmaydi."},
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ObjectDoesNotExist):
        logger.warning("ObjectDoesNotExist: %s", exc)
        return Response(
            {"detail": "So'ralgan ma'lumot topilmadi."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, IntegrityError):
        logger.error("IntegrityError: %s", exc, exc_info=True)
        return Response(
            {"detail": "Ma'lumot bazasi cheklovi buzildi (takroriy yoki bog'liq yozuv)."},
            status=status.HTTP_409_CONFLICT,
        )

    # Qolganini DRF/Django o'zi 500 qilib qaytaradi (va logga yozadi)
    return None
