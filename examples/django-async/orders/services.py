"""The whole transaction stays in one synchronous, thread-sensitive helper."""

from django.db import transaction

from .models import Order, Shipment


class InvalidTransition(Exception):
    """A cancelled order cannot become shipped."""


def ship_order(order_id, owner_id):
    """Commit both rows together and return evaluated data, retaining safe retry identity."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id, owner_id=owner_id)
        if order.status not in {"pending", "shipped"}:
            raise InvalidTransition
        if order.status == "pending":
            order.status = "shipped"
            order.save(update_fields=["status"])
        shipment, _ = Shipment.objects.get_or_create(order=order)
        return {"id": str(order.pk), "status": order.status, "shipment_id": str(shipment.pk)}
