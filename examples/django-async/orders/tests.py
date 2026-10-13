"""Observable async boundary tests; SQLite does not establish row-lock guarantees."""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import AsyncClient, Client, TestCase

from .models import Order, Shipment
from .services import ship_order


class OrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(username="owner")
        cls.other = get_user_model().objects.create_user(username="other")
        cls.order = Order.objects.create(owner=cls.owner)
        cls.cancelled = Order.objects.create(owner=cls.owner, status="cancelled")

    async def authenticated(self, user=None):
        client = AsyncClient()
        await client.aforce_login(user or self.owner)
        return client

    async def test_owner_reads_evaluated_data(self):
        client = await self.authenticated()
        response = await client.get(f"/orders/{self.order.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": str(self.order.pk), "status": "pending"})

    async def test_anonymous_read_is_denied(self):
        response = await AsyncClient().get(f"/orders/{self.order.pk}/")
        self.assertEqual(response.status_code, 401)

    async def test_wrong_owner_cannot_read_or_ship(self):
        client = await self.authenticated(self.other)
        self.assertEqual((await client.get(f"/orders/{self.order.pk}/")).status_code, 404)
        self.assertEqual((await client.post(f"/orders/{self.order.pk}/ship/")).status_code, 404)
        await self.order.arefresh_from_db()
        self.assertEqual(self.order.status, "pending")
        self.assertFalse(await Shipment.objects.filter(order=self.order).aexists())

    async def test_missing_order_does_not_leak_data(self):
        client = await self.authenticated()
        self.assertEqual((await client.get(f"/orders/{uuid.uuid4()}/")).status_code, 404)

    async def test_safe_retry_reuses_one_shipment(self):
        client = await self.authenticated()
        first = await client.post(f"/orders/{self.order.pk}/ship/")
        second = await client.post(f"/orders/{self.order.pk}/ship/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(await Shipment.objects.filter(order=self.order).acount(), 1)

    async def test_cancelled_order_rejects_transition(self):
        client = await self.authenticated()
        response = await client.post(f"/orders/{self.cancelled.pk}/ship/")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(await Shipment.objects.filter(order=self.cancelled).aexists())

    async def test_shipping_requires_post(self):
        client = await self.authenticated()
        self.assertEqual((await client.get(f"/orders/{self.order.pk}/ship/")).status_code, 405)

    def test_cookie_authenticated_write_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(f"/orders/{self.order.pk}/ship/").status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_second_write_failure_rolls_back_the_first(self):
        with patch("orders.services.Shipment.objects.get_or_create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                ship_order(self.order.pk, self.owner.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        self.assertFalse(Shipment.objects.filter(order=self.order).exists())
