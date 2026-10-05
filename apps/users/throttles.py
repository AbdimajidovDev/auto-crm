"""
apps/users/throttles.py

Authentication rate limiting & brute-force mitigation throttles:
1. LoginIpThrottle: Limits login attempts per client IP.
2. LoginIdentifierThrottle: Limits login attempts per phone number (target account).
3. PasswordResetRequestIpThrottle: Limits forgot-password requests per client IP.
4. PasswordResetRequestEmailThrottle: Limits forgot-password requests per email address.
5. PasswordResetConfirmThrottle: Limits token reset attempts per client IP / uid.
6. OtpRateThrottle: Limits OTP resend requests (defense-in-depth).
"""

import hashlib
import re

from django.core.cache import cache
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from core.middleware.audit import client_ip


class BaseAuthRateThrottle(SimpleRateThrottle):
    """
    Base throttle that dynamically reads from api_settings.DEFAULT_THROTTLE_RATES
    to allow dynamic setting overrides in tests and deployments,
    and normalizes cache keys to prevent cache-injection / formatting issues.
    """
    cache = cache
    default_rate = None

    def get_rate(self):
        # 1. Check api_settings.DEFAULT_THROTTLE_RATES (updated dynamically with settings)
        rates = getattr(api_settings, 'DEFAULT_THROTTLE_RATES', {}) or {}
        if self.scope in rates and rates[self.scope] is not None:
            return rates[self.scope]
        # 2. Check class-level THROTTLE_RATES dict
        if hasattr(self, 'THROTTLE_RATES') and self.scope in self.THROTTLE_RATES:
            return self.THROTTLE_RATES[self.scope]
        # 3. Check fallback default_rate
        if self.default_rate:
            return self.default_rate
        # 4. Standard DRF resolution
        return super().get_rate()

    def get_client_ip(self, request):
        return client_ip(request) or "127.0.0.1"


class LoginIpThrottle(BaseAuthRateThrottle):
    """
    Throttles login requests based on client IP to mitigate broad credential stuffing.
    Default rate: 10/min
    """
    scope = 'login_ip'
    default_rate = '10/min'

    def get_cache_key(self, request, view):
        ip = self.get_client_ip(request)
        ident = ip.replace(" ", "").replace(":", "_")
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class LoginIdentifierThrottle(BaseAuthRateThrottle):
    """
    Throttles login requests based on the submitted phone_number to mitigate
    targeted brute-force attacks against specific accounts across multiple IPs.
    Default rate: 5/min
    """
    scope = 'login_identifier'
    default_rate = '5/min'

    def get_cache_key(self, request, view):
        phone_number = None
        try:
            if isinstance(request.data, dict):
                phone_number = request.data.get('phone_number')
        except Exception:
            phone_number = None

        if not phone_number:
            return None

        # Normalize phone: extract digits with optional leading plus
        raw = str(phone_number).strip()
        normalized = re.sub(r'[^\d+]', '', raw)
        if not normalized:
            return None

        ident = normalized.replace('+', 'p')
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class PasswordResetRequestIpThrottle(BaseAuthRateThrottle):
    """
    Throttles forgot-password requests by IP to prevent SMTP abuse / request flooding.
    Default rate: 10/hour
    """
    scope = 'password_reset_request_ip'
    default_rate = '10/hour'

    def get_cache_key(self, request, view):
        ip = self.get_client_ip(request)
        ident = ip.replace(" ", "").replace(":", "_")
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class PasswordResetRequestEmailThrottle(BaseAuthRateThrottle):
    """
    Throttles forgot-password requests by target email to prevent email bombing / harassment.
    Default rate: 3/hour
    """
    scope = 'password_reset_request_email'
    default_rate = '3/hour'

    def get_cache_key(self, request, view):
        email = None
        try:
            if isinstance(request.data, dict):
                email = request.data.get('email')
        except Exception:
            email = None

        if not email:
            return None

        normalized_email = str(email).strip().lower()
        if not normalized_email:
            return None

        ident = hashlib.sha256(normalized_email.encode('utf-8')).hexdigest()[:32]
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class PasswordResetConfirmThrottle(BaseAuthRateThrottle):
    """
    Throttles reset-password confirmation attempts to prevent token brute-forcing.
    Default rate: 5/min
    """
    scope = 'password_reset_confirm'
    default_rate = '5/min'

    def get_cache_key(self, request, view):
        ip = self.get_client_ip(request)
        uidb64 = view.kwargs.get('uidb64') if hasattr(view, 'kwargs') and view.kwargs else ''
        if uidb64:
            ident = f"{ip.replace(':', '_')}_{uidb64}"
        else:
            ident = ip.replace(':', '_')
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class OtpRateThrottle(BaseAuthRateThrottle):
    """
    Throttles OTP generation and resend attempts (for defense-in-depth and future use).
    Default rate: 3/min
    """
    scope = 'otp_resend'
    default_rate = '3/min'

    def get_cache_key(self, request, view):
        ip = self.get_client_ip(request)
        target = None
        try:
            if isinstance(request.data, dict):
                target = request.data.get('phone_number') or request.data.get('email')
        except Exception:
            target = None

        if target:
            target_hash = hashlib.sha256(str(target).strip().lower().encode('utf-8')).hexdigest()[:16]
            ident = f"{ip.replace(':', '_')}_{target_hash}"
        else:
            ident = ip.replace(':', '_')

        return self.cache_format % {'scope': self.scope, 'ident': ident}
