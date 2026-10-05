"""
apps/users/test_auth_throttling.py

Comprehensive security & rate-limiting test suite covering 21 scenarios:
- LOGIN:
    1. allowed attempts ishlaydi
    2. limitdan oshganda 429
    3. throttle key kutilgan scope bo'yicha ishlaydi
    4. successful login throttlingni noto'g'ri reset qilmasligi kerak
    5. authenticated user uchun login endpoint behavior tekshirilsin
- PASSWORD RESET REQUEST:
    6. allowed requests ishlaydi
    7. limitdan oshganda 429
    8. same account/email abuse throttled
    9. IP abuse throttled
    10. unknown email response enumeration bermaydi
- OTP / RESEND:
    11. resend limit ishlaydi
    12. repeated OTP requests throttled
    13. valid OTP flow buzilmagan
- RESET PASSWORD:
    14. invalid token attempts throttled
    15. expired/invalid token behavior saqlangan
    16. valid reset flow ishlaydi
    17. token replay ishlamasligi tekshirilsin
- REGRESSION:
    18. normal login flow ishlaydi
    19. normal forgot-password flow ishlaydi
    20. normal reset-password flow ishlaydi
    21. boshqa API endpointlar throttle sabab buzilmaydi
"""

import re
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from apps.users.models import User
from apps.users.throttles import (
    LoginIdentifierThrottle,
    LoginIpThrottle,
    OtpRateThrottle,
    PasswordResetConfirmThrottle,
    PasswordResetRequestEmailThrottle,
    PasswordResetRequestIpThrottle,
)


class DummyOtpView(APIView):
    """Simulated OTP endpoint to verify OtpRateThrottle behavior."""
    permission_classes = ()
    authentication_classes = ()
    throttle_classes = [OtpRateThrottle]

    def post(self, request):
        return Response({"detail": "OTP sent successfully."}, status=status.HTTP_200_OK)


class AuthThrottlingSecurityTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.client = APIClient()

        # Create test users
        self.password = "ValidSecretPass123!"
        self.user = User.objects.create_user(
            phone_number="+998901234567",
            email="user1@example.com",
            full_name="Test User One",
            password=self.password,
        )
        self.user2 = User.objects.create_user(
            phone_number="+998909876543",
            email="user2@example.com",
            full_name="Test User Two",
            password=self.password,
        )

    def tearDown(self):
        cache.clear()
        super().tearDown()

    # =========================================================================
    # LOGIN THROTTLING TESTS (1 - 5)
    # =========================================================================

    def test_01_login_allowed_attempts(self):
        """1. Allowed login attempts within rate limit succeed or return standard validation error (not 429)."""
        # 4 failed attempts within the 5/min limit
        for i in range(4):
            resp = self.client.post(
                "/api/users/login/",
                {"phone_number": "+998901234567", "password": "WrongPassword123!"},
                format="json",
                REMOTE_ADDR="192.168.1.10",
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("message", resp.data)
            msg_str = str(resp.data["message"])
            self.assertIn("phone_number yoki parol noto'g'ri!", msg_str)

    def test_02_login_exceeded_limit_returns_429(self):
        """2. When login rate limit is exceeded, endpoint returns 429 Too Many Requests with Retry-After header."""
        # 5 allowed attempts for this identifier
        for i in range(5):
            resp = self.client.post(
                "/api/users/login/",
                {"phone_number": "+998901234567", "password": "WrongPassword123!"},
                format="json",
                REMOTE_ADDR="192.168.1.11",
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 6th attempt must be throttled
        resp6 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998901234567", "password": "WrongPassword123!"},
            format="json",
            REMOTE_ADDR="192.168.1.11",
        )
        self.assertEqual(resp6.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("detail", resp6.data)
        self.assertIn("Request was throttled", str(resp6.data["detail"]))
        self.assertTrue(resp6.has_header("Retry-After"))

    def test_03_login_throttle_keys_scope(self):
        """3. Throttle keys isolate quotas properly across different IPs and identifiers."""
        # Exhaust quota for user1 from IP_A
        for i in range(5):
            self.client.post(
                "/api/users/login/",
                {"phone_number": "+998901234567", "password": "WrongPassword123!"},
                format="json",
                REMOTE_ADDR="192.168.1.20",
            )
        # user1 is now throttled
        resp_user1 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998901234567", "password": "WrongPassword123!"},
            format="json",
            REMOTE_ADDR="192.168.1.20",
        )
        self.assertEqual(resp_user1.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # But user2 from a different IP is NOT throttled (distinct quota)
        resp_user2 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998909876543", "password": "WrongPassword123!"},
            format="json",
            REMOTE_ADDR="192.168.1.21",
        )
        self.assertEqual(resp_user2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_04_successful_login_does_not_reset_throttle(self):
        """4. Successful login does NOT wipe throttle history; rate limit continues to count total requests."""
        # 4 failed attempts
        for _ in range(4):
            resp = self.client.post(
                "/api/users/login/",
                {"phone_number": "+998901234567", "password": "WrongPassword123!"},
                format="json",
                REMOTE_ADDR="192.168.1.30",
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 5th attempt is successful
        resp5 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998901234567", "password": self.password},
            format="json",
            REMOTE_ADDR="192.168.1.30",
        )
        self.assertEqual(resp5.status_code, status.HTTP_200_OK)

        # 6th attempt immediately after must be throttled (identifier limit is 5/min)
        resp6 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998901234567", "password": self.password},
            format="json",
            REMOTE_ADDR="192.168.1.30",
        )
        self.assertEqual(resp6.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_05_authenticated_user_login_throttled(self):
        """5. Authenticated user/client is still subjected to login rate limiting."""
        self.client.force_authenticate(user=self.user)
        for i in range(5):
            self.client.post(
                "/api/users/login/",
                {"phone_number": "+998901234567", "password": self.password},
                format="json",
                REMOTE_ADDR="192.168.1.40",
            )
        resp6 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998901234567", "password": self.password},
            format="json",
            REMOTE_ADDR="192.168.1.40",
        )
        self.assertEqual(resp6.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # =========================================================================
    # PASSWORD RESET REQUEST THROTTLING TESTS (6 - 10)
    # =========================================================================

    def test_06_forgot_password_allowed_requests(self):
        """6. Allowed forgot-password requests within limit succeed with 200 OK."""
        for i in range(2):
            resp = self.client.post(
                "/api/users/auth/forgot-password/",
                {"email": "user1@example.com"},
                format="json",
                REMOTE_ADDR="192.168.2.10",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertIn("detail", resp.data)

    def test_07_forgot_password_exceeded_limit_returns_429(self):
        """7. Exceeding forgot-password limit returns HTTP 429."""
        # Limit is 3/hour per email
        for i in range(3):
            resp = self.client.post(
                "/api/users/auth/forgot-password/",
                {"email": "user1@example.com"},
                format="json",
                REMOTE_ADDR=f"192.168.2.{10 + i}",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        resp4 = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "user1@example.com"},
            format="json",
            REMOTE_ADDR="192.168.2.99",
        )
        self.assertEqual(resp4.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_08_forgot_password_same_email_abuse_throttled(self):
        """8. Repeated requests for the same target email are throttled even from multiple IPs."""
        # Attacker tries from 3 different IPs targeting user1@example.com
        for i in range(3):
            resp = self.client.post(
                "/api/users/auth/forgot-password/",
                {"email": "user1@example.com"},
                format="json",
                REMOTE_ADDR=f"10.0.0.{i + 1}",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # 4th request from yet another IP is blocked by email throttle
        resp4 = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "user1@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.100",
        )
        self.assertEqual(resp4.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_09_forgot_password_ip_abuse_throttled(self):
        """9. Flooding from a single IP across different emails is throttled by IP limit (10/hour)."""
        single_ip = "192.168.5.50"
        for i in range(10):
            resp = self.client.post(
                "/api/users/auth/forgot-password/",
                {"email": f"victim{i}@example.com"},
                format="json",
                REMOTE_ADDR=single_ip,
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # 11th request from same IP is throttled
        resp11 = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "victim11@example.com"},
            format="json",
            REMOTE_ADDR=single_ip,
        )
        self.assertEqual(resp11.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_10_forgot_password_unknown_email_anti_enumeration(self):
        """10. Unregistered email returns identical 200 OK and message as registered email."""
        resp_existing = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "user1@example.com"},
            format="json",
            REMOTE_ADDR="192.168.6.10",
        )
        resp_unknown = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "nonexistent_random_person@example.com"},
            format="json",
            REMOTE_ADDR="192.168.6.11",
        )
        self.assertEqual(resp_existing.status_code, resp_unknown.status_code)
        self.assertEqual(resp_existing.data, resp_unknown.data)
        self.assertEqual(resp_unknown.status_code, status.HTTP_200_OK)

    # =========================================================================
    # OTP / RESEND THROTTLING TESTS (11 - 13)
    # =========================================================================

    def test_11_otp_resend_limit_enforced(self):
        """11. OtpRateThrottle enforces 3 requests per minute limit."""
        factory = APIRequestFactory()
        view = DummyOtpView.as_view()

        # 3 requests pass
        for i in range(3):
            req = factory.post("/otp/", {"phone_number": "+998901234567"}, format="json", REMOTE_ADDR="192.168.7.1")
            resp = view(req)
            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # 4th request throttled
        req4 = factory.post("/otp/", {"phone_number": "+998901234567"}, format="json", REMOTE_ADDR="192.168.7.1")
        resp4 = view(req4)
        self.assertEqual(resp4.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_12_otp_resend_repeated_requests_throttled(self):
        """12. Repeated rapid OTP requests are throttled with 429."""
        factory = APIRequestFactory()
        view = DummyOtpView.as_view()

        for _ in range(3):
            req = factory.post("/otp/", {"email": "user1@example.com"}, format="json", REMOTE_ADDR="192.168.7.2")
            view(req)

        req_blocked = factory.post("/otp/", {"email": "user1@example.com"}, format="json", REMOTE_ADDR="192.168.7.2")
        resp_blocked = view(req_blocked)
        self.assertEqual(resp_blocked.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_13_otp_valid_flow_unaffected(self):
        """13. Normal, non-abusive OTP requests within rate limit are accepted."""
        factory = APIRequestFactory()
        view = DummyOtpView.as_view()

        req = factory.post("/otp/", {"phone_number": "+998909876543"}, format="json", REMOTE_ADDR="192.168.7.3")
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["detail"], "OTP sent successfully.")

    # =========================================================================
    # RESET PASSWORD THROTTLING & TOKEN SECURITY TESTS (14 - 17)
    # =========================================================================

    def test_14_reset_password_invalid_token_throttled(self):
        """14. Rapid invalid token guessing attempts are throttled after 5 attempts."""
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        for i in range(5):
            resp = self.client.post(
                f"/api/users/auth/reset-password/{uidb64}/fake-token-{i}/",
                {"password": "NewSecretPass123!", "confirm_password": "NewSecretPass123!"},
                format="json",
                REMOTE_ADDR="192.168.8.10",
            )
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 6th attempt must be throttled
        resp6 = self.client.post(
            f"/api/users/auth/reset-password/{uidb64}/fake-token-6/",
            {"password": "NewSecretPass123!", "confirm_password": "NewSecretPass123!"},
            format="json",
            REMOTE_ADDR="192.168.8.10",
        )
        self.assertEqual(resp6.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_15_reset_password_expired_or_invalid_token_behavior_preserved(self):
        """15. Invalid or malformed token returns 400 Bad Request."""
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        resp = self.client.post(
            f"/api/users/auth/reset-password/{uidb64}/invalid-token/",
            {"password": "NewSecretPass123!", "confirm_password": "NewSecretPass123!"},
            format="json",
            REMOTE_ADDR="192.168.8.20",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)
        self.assertEqual(resp.data["detail"], "Token is invalid or expired.")

    def test_16_reset_password_valid_flow_succeeds(self):
        """16. Valid reset token allows successful password update."""
        token_generator = PasswordResetTokenGenerator()
        token = token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        new_password = "BrandNewValidPass456!"

        resp = self.client.post(
            f"/api/users/auth/reset-password/{uidb64}/{token}/",
            {"password": new_password, "confirm_password": new_password},
            format="json",
            REMOTE_ADDR="192.168.8.30",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["detail"], "Password successfully reset.")

        # Verify new password works in DB
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

    def test_17_reset_password_token_replay_fails(self):
        """17. Replaying an already-used reset token fails with 400 Bad Request."""
        token_generator = PasswordResetTokenGenerator()
        token = token_generator.make_token(self.user)
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        new_password = "FirstUpdatedPassword789!"

        # First use succeeds
        resp1 = self.client.post(
            f"/api/users/auth/reset-password/{uidb64}/{token}/",
            {"password": new_password, "confirm_password": new_password},
            format="json",
            REMOTE_ADDR="192.168.8.40",
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)

        # Second use (replay attack) fails
        resp2 = self.client.post(
            f"/api/users/auth/reset-password/{uidb64}/{token}/",
            {"password": "SecondAttemptPass999!", "confirm_password": "SecondAttemptPass999!"},
            format="json",
            REMOTE_ADDR="192.168.8.40",
        )
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp2.data["detail"], "Token is invalid or expired.")

    # =========================================================================
    # REGRESSION TESTS (18 - 21)
    # =========================================================================

    def test_18_normal_login_flow(self):
        """18. Normal login succeeds, returns user metadata, and sets auth cookies."""
        resp = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998901234567", "password": self.password},
            format="json",
            REMOTE_ADDR="192.168.9.10",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["success"])
        self.assertEqual(resp.data["phone_number"], "+998901234567")
        self.assertIn("access_token", resp.cookies)
        self.assertIn("refresh_token", resp.cookies)

    def test_19_normal_forgot_password_flow(self):
        """19. Normal forgot-password request sends email containing reset link."""
        mail.outbox = []
        resp = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "user1@example.com"},
            format="json",
            REMOTE_ADDR="192.168.9.20",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("user1@example.com", mail.outbox[0].to)
        self.assertIn("reset-password", mail.outbox[0].body)

    def test_20_normal_reset_password_flow(self):
        """20. Complete end-to-end forgot-password -> extract link -> reset password -> login works cleanly."""
        mail.outbox = []
        # Step 1: Request reset
        resp1 = self.client.post(
            "/api/users/auth/forgot-password/",
            {"email": "user2@example.com"},
            format="json",
            REMOTE_ADDR="192.168.9.30",
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

        # Step 2: Extract uidb64 and token from email body
        email_body = mail.outbox[0].body
        match = re.search(r"/reset-password/([^/]+)/([^/]+)/", email_body)
        self.assertIsNotNone(match, "Reset link not found in email body")
        uidb64, token = match.group(1), match.group(2)

        # Step 3: Reset password
        brand_new_pass = "ResetFlowPassSuccess999!"
        resp2 = self.client.post(
            f"/api/users/auth/reset-password/{uidb64}/{token}/",
            {"password": brand_new_pass, "confirm_password": brand_new_pass},
            format="json",
            REMOTE_ADDR="192.168.9.31",
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)

        # Step 4: Login with new password
        resp3 = self.client.post(
            "/api/users/login/",
            {"phone_number": "+998909876543", "password": brand_new_pass},
            format="json",
            REMOTE_ADDR="192.168.9.32",
        )
        self.assertEqual(resp3.status_code, status.HTTP_200_OK)
        self.assertTrue(resp3.data["success"])

    def test_21_other_api_endpoints_not_throttled(self):
        """21. Non-auth API endpoints are not throttled by authentication rate limits."""
        self.client.force_authenticate(user=self.user)
        # Send 15 consecutive requests to /api/users/profile/
        for _ in range(15):
            resp = self.client.get("/api/users/profile/", REMOTE_ADDR="192.168.9.40")
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
