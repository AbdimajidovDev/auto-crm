"""
apps/users/test_user_deletion_security.py

Comprehensive security, admin integrity, and concurrency test suite for P1 #4:
1. unauthorized user cannot delete another user
2. authorized admin can delete allowed user
3. superuser protection (non-superuser cannot delete superuser; last superuser cannot be deleted)
4. self-delete behavior (user cannot delete own account)
5. last-admin protection (last remaining active admin cannot be deleted)
6. multiple-admin deletion (deletion succeeds when other active admins exist)
7. inactive admin behavior (inactive admin can be deleted safely)
8. FK/history integrity (ProtectedError on user with sales -> 409 Conflict)
9. concurrent admin deletion race (select_for_update prevents lockout race condition)
10. existing successful delete flow (cascades StoreUser, deletes user)
11. API contract verification (status codes 204, 400, 403, 404, 409 and {"detail": "..."})
"""

import threading
from django.db import connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.sales.models import Sale
from apps.store.models import Store, StoreUser
from apps.users.models import Role, User
from apps.users.services import UserService


class UserDeletionSecurityTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

        # 1. Platform Superuser
        self.superuser = User.objects.create(
            phone_number="+998901111111",
            full_name="Platform Superuser",
            is_superuser=True,
            is_staff=True,
            is_active=True,
        )

        # 2. Admin Role & Admin Users
        self.admin_role = Role.objects.create(
            name="User Admin Role",
            permissions=["users.view", "users.delete"],
        )
        self.admin_user_1 = User.objects.create(
            phone_number="+998902222221",
            full_name="Admin One",
            is_superuser=False,
            is_staff=False,
            role=self.admin_role,
            is_active=True,
        )
        self.admin_user_2 = User.objects.create(
            phone_number="+998902222222",
            full_name="Admin Two",
            is_superuser=False,
            is_staff=False,
            role=self.admin_role,
            is_active=True,
        )

        # 3. Viewer Role & Non-privileged User
        self.viewer_role = Role.objects.create(
            name="Viewer Role",
            permissions=["users.view"],
        )
        self.regular_user = User.objects.create(
            phone_number="+998903333333",
            full_name="Regular Viewer",
            is_superuser=False,
            role=self.viewer_role,
            is_active=True,
        )

        # 4. Standard Store and Target Seller
        self.store = Store.objects.create(name="Central Store")
        self.target_seller = User.objects.create(
            phone_number="+998904444444",
            full_name="Target Seller",
            is_superuser=False,
            role=None,
            is_active=True,
        )
        StoreUser.objects.create(user=self.target_seller, store=self.store, role="s", is_active=True)

    def test_01_unauthorized_user_cannot_delete_another_user(self):
        """1. Unauthorized user without 'users.delete' permission cannot delete another user (403 Forbidden)."""
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.delete(f"/api/users/{self.target_seller.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.target_seller.pk).exists())

    def test_02_authorized_admin_can_delete_allowed_user(self):
        """2. Authorized admin with 'users.delete' can delete an allowed non-admin user with no sales (204)."""
        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{self.target_seller.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.target_seller.pk).exists())

    def test_03_superuser_protection(self):
        """3. Non-superuser cannot delete superuser; last active superuser cannot be deleted."""
        # A. Non-superuser admin tries to delete superuser -> 403 Forbidden
        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{self.superuser.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", resp.data)
        self.assertIn("Superuser", str(resp.data["detail"]))
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())

        # B. Superuser tries to delete the ONLY active superuser -> 400 Bad Request
        another_admin = User.objects.create(
            phone_number="+998909999999",
            full_name="Another Admin",
            is_superuser=True,
            is_staff=True,
            is_active=True,
        )
        # Now there are 2 superusers: self.superuser and another_admin.
        # Authenticate as another_admin and delete self.superuser -> allowed
        self.client.force_authenticate(user=another_admin)
        resp_del = self.client.delete(f"/api/users/{self.superuser.pk}/")
        self.assertEqual(resp_del.status_code, status.HTTP_204_NO_CONTENT)

        # Now only another_admin remains as the last superuser.
        # Create a second admin to attempt deleting the last superuser:
        second_super = User.objects.create(
            phone_number="+998908888888",
            full_name="Second Super",
            is_superuser=True,
            is_active=True,
        )
        # Deactivate another_admin so second_super is the only active one
        another_admin.is_active = False
        another_admin.save()

        # Now only second_super remains as the sole active superuser. Trying to delete it must fail:
        with self.assertRaises(Exception) as ctx:
            UserService.delete_user(second_super.pk, requesting_user=None)
        self.assertIn("oxirgi faol superuser", str(ctx.exception).lower())

    def test_04_self_delete_protection(self):
        """4. User cannot delete their own account (400 Bad Request)."""
        # Admin 1 attempts to delete Admin 1
        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{self.admin_user_1.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)
        self.assertIn("o'z hisobini", str(resp.data["detail"]))
        self.assertTrue(User.objects.filter(pk=self.admin_user_1.pk).exists())

        # Superuser attempts to delete Superuser
        self.client.force_authenticate(user=self.superuser)
        resp_su = self.client.delete(f"/api/users/{self.superuser.pk}/")
        self.assertEqual(resp_su.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())

    def test_05_last_admin_protection(self):
        """5. Attempting to delete the last active administrator fails with 400 Bad Request."""
        # Deactivate superuser and admin_user_2 so admin_user_1 is the sole remaining admin
        self.superuser.is_active = False
        self.superuser.save()
        self.admin_user_2.is_active = False
        self.admin_user_2.save()

        # Create a separate caller with users.delete permission so it's not a self-deletion
        # but admin_user_1 is the only admin marked active in the system:
        external_actor = User.objects.create(
            phone_number="+998905555555",
            full_name="External Admin Actor",
            is_superuser=True,
            is_active=True,
        )
        # Now make external_actor the caller, but deactivate external_actor right before checking
        # Better: create a scenario where target is the ONLY active admin in DB
        User.objects.filter(pk__in=[self.superuser.pk, self.admin_user_2.pk, external_actor.pk]).update(is_active=False)
        # Now only admin_user_1 is active
        self.client.force_authenticate(user=external_actor)
        resp = self.client.delete(f"/api/users/{self.admin_user_1.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp.data)
        self.assertIn("oxirgi faol administrator", str(resp.data["detail"]))
        self.assertTrue(User.objects.filter(pk=self.admin_user_1.pk, is_active=True).exists())

    def test_06_multiple_admin_deletion(self):
        """6. When multiple active admins exist, an admin CAN delete another admin (not self, not superuser)."""
        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{self.admin_user_2.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.admin_user_2.pk).exists())
        # admin_user_1 and superuser remain active
        self.assertTrue(User.objects.filter(pk=self.admin_user_1.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())

    def test_07_inactive_admin_behavior(self):
        """7. An inactive admin (is_active=False) can be deleted without triggering last-active-admin block."""
        inactive_admin = User.objects.create(
            phone_number="+998906666666",
            full_name="Inactive Admin",
            role=self.admin_role,
            is_active=False,
        )
        # Even if superuser and admin_user_2 are inactive:
        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{inactive_admin.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=inactive_admin.pk).exists())

    def test_08_fk_history_integrity_protected(self):
        """8. Attempting to delete a user with associated sales records returns 409 Conflict (ProtectedError)."""
        # Create a sale associated with target_seller
        Sale.objects.create(
            seller=self.target_seller,
            store=self.store,
            total_amount=50000,
        )

        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{self.target_seller.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("detail", resp.data)
        self.assertIn("sotuvlar yoki boshqa ma'lumotlar", str(resp.data["detail"]))
        # Target seller still exists
        self.assertTrue(User.objects.filter(pk=self.target_seller.pk).exists())

    def test_10_existing_successful_delete_flow(self):
        """10. Successful delete cascades StoreUser associations and removes user record completely."""
        seller_id = self.target_seller.pk
        self.assertTrue(StoreUser.objects.filter(user_id=seller_id).exists())

        self.client.force_authenticate(user=self.admin_user_1)
        resp = self.client.delete(f"/api/users/{seller_id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        # Both User and StoreUser records are cleaned up
        self.assertFalse(User.objects.filter(pk=seller_id).exists())
        self.assertFalse(StoreUser.objects.filter(user_id=seller_id).exists())

    def test_11_api_contract_verification(self):
        """11. Verify all API contract responses (404, 403, 400, 409, 204) adhere to standard error shapes."""
        self.client.force_authenticate(user=self.admin_user_1)

        # 404 Not Found for nonexistent user
        resp_404 = self.client.delete("/api/users/999999/")
        self.assertEqual(resp_404.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", resp_404.data)
        self.assertIn("topilmadi", str(resp_404.data["detail"]))

        # 400 Bad Request for self-delete
        resp_400 = self.client.delete(f"/api/users/{self.admin_user_1.pk}/")
        self.assertEqual(resp_400.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", resp_400.data)

        # 403 Forbidden for superuser deletion by non-superuser
        resp_403 = self.client.delete(f"/api/users/{self.superuser.pk}/")
        self.assertEqual(resp_403.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", resp_403.data)


class ConcurrentAdminDeletionRaceTests(TransactionTestCase):
    """
    9. Concurrency test: Verifies that select_for_update locking prevents
    race conditions where two admins simultaneously delete each other,
    which would leave 0 active admins in the system.
    """

    def setUp(self):
        super().setUp()
        # Clean any leftover active admins
        User.objects.all().delete()
        Role.objects.all().delete()

        self.admin_role = Role.objects.create(
            name="Concurrent Admin Role",
            permissions=["users.view", "users.delete"],
        )
        self.admin_a = User.objects.create(
            phone_number="+998901000001",
            full_name="Admin A",
            is_superuser=False,
            role=self.admin_role,
            is_active=True,
        )
        self.admin_b = User.objects.create(
            phone_number="+998901000002",
            full_name="Admin B",
            is_superuser=False,
            role=self.admin_role,
            is_active=True,
        )

    def test_09_concurrent_admin_deletion_race(self):
        """
        Admin A attempts to delete Admin B, and Admin B attempts to delete Admin A simultaneously.
        Only ONE deletion must succeed, and the second MUST fail with last-admin protection,
        guaranteeing that at least 1 active admin remains in the system.
        """
        results = []
        errors = []

        def worker_delete_b():
            connection.close()
            try:
                UserService.delete_user(user_id=self.admin_b.pk, requesting_user=self.admin_a)
                results.append("A_deleted_B_success")
            except Exception as e:
                errors.append(f"A_deleted_B_error: {e}")
            finally:
                connection.close()

        def worker_delete_a():
            connection.close()
            try:
                UserService.delete_user(user_id=self.admin_a.pk, requesting_user=self.admin_b)
                results.append("B_deleted_A_success")
            except Exception as e:
                errors.append(f"B_deleted_A_error: {e}")
            finally:
                connection.close()

        t1 = threading.Thread(target=worker_delete_b)
        t2 = threading.Thread(target=worker_delete_a)

        t1.start()
        t2.start()

        t1.join()
        t2.join()

        # At least one admin must remain active in DB
        active_remaining = User.objects.filter(is_active=True).count()
        self.assertGreaterEqual(active_remaining, 1, "CRITICAL: Race condition caused complete admin lockout!")

        # Exactly one deletion must succeed, and one must fail
        self.assertEqual(len(results), 1, f"Expected exactly 1 success, got {len(results)}: {results}")
        self.assertEqual(len(errors), 1, f"Expected exactly 1 error, got {len(errors)}: {errors}")
        self.assertIn("oxirgi faol administrator", errors[0].lower())
