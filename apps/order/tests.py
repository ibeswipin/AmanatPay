from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.client.models import Client
from apps.merchant.models import Merchant
from apps.order.choices import OrderStatus
from apps.order.models import Order
from services.order import InstallmentError, build_schedule


class InstallmentTest(TestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(name="Shop", inn="123", commission_percent=5)
        self.client_obj = Client.objects.create(full_name="Иванов", phone="+998901234567", limit=5000)
        self.order = Order.objects.create(
            merchant=self.merchant, client=self.client_obj,
            amount=Decimal("1000"), months=3, markup_percent=10,
        )

    def test_schedule_sums_to_total(self):
        payments = list(build_schedule(self.order))

        self.assertEqual(self.order.total, Decimal("1100.00"))
        self.assertEqual(len(payments), 3)
        self.assertEqual(sum(p.amount for p in payments), self.order.total)
        self.assertEqual(self.order.status, OrderStatus.ACTIVE)
        self.assertEqual(self.order.merchant_payout, Decimal("950.00"))

    def test_limit_enforced(self):
        self.client_obj.limit = Decimal("100")
        self.client_obj.save()
        with self.assertRaises(InstallmentError):
            build_schedule(self.order)

    def test_paying_all_closes_order(self):
        for payment in build_schedule(self.order):
            payment.pay()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CLOSED)
        self.assertEqual(self.order.remainder, Decimal("0.00"))
        self.assertEqual(self.client_obj.debt, Decimal("0"))

    def test_api_schedule_endpoint(self):
        api = APIClient()
        response = api.post(f"/api/v1/orders/{self.order.uuid}/schedule/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(response.data["payments"]), 3)
