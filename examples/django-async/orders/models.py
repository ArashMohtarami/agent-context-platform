"""Small scoped domain: one owner per order and at most one shipment per order."""

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models


class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=16, default="pending")

    class Meta:
        constraints: ClassVar[list[models.CheckConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "shipped", "cancelled"]),
                name="demo_order_valid_status",
            )
        ]


class Shipment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE)
