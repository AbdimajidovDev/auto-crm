from django.db import models
from django.conf import settings


class UserHistory(models.Model):

    class ActionType(models.TextChoices):
        LOGIN = "li", "Login"
        LOGOUT = "lo", "Logout"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="history"
    )
    action = models.CharField(max_length=10, choices=ActionType.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user.email} - {self.action} - {self.created_at}"
