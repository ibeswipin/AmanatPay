import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.order.models import RadiusOrder, RadiusOrderProduct


class Command(BaseCommand):
    help = 'JSON fayldan RadiusCRM buyurtmalarini import qilish'

    @staticmethod
    def _parse_created_at(value):
        if not value:
            return None
        return parse_datetime(value)

    def add_arguments(self, parser):
        default_path = Path(__file__).resolve().parents[4] / "radius_orders.json"
        parser.add_argument(
            'json_path',
            nargs='?',
            default=str(default_path),
            type=str,
            help='JSON fayl yuli. Default: project/radius_orders.json',
        )

    def handle(self, *args, **options):
        json_path = options['json_path']

        if not os.path.exists(json_path):
            self.stdout.write(self.style.ERROR(f"Fayl topilmadi: {json_path}"))
            return

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        imported_count = 0
        with transaction.atomic():
            for item in data:
                number = item.get('number')
                external_id = item['external_id']
                order_created_at = self._parse_created_at(item.get('created_at'))

                order = None
                if number:
                    order = RadiusOrder.objects.filter(number=number).first()
                if order is None:
                    order = RadiusOrder.objects.filter(external_id=external_id).first()

                defaults = {
                    'external_id': external_id,
                    'number': number,
                    'price': item.get('price'),
                    'status': item.get('status'),
                    'type': item.get('type'),
                    'month': item.get('month'),
                    'is_paid': item.get('is_paid', False),
                    'pinfl': item.get('pinfl'),
                    'phone': item.get('phone'),
                }

                if order is None:
                    order = RadiusOrder.objects.create(
                        **defaults,
                        created_at=order_created_at,
                    )
                else:
                    for field, value in defaults.items():
                        setattr(order, field, value)
                    if order_created_at:
                        order.created_at = order_created_at
                    order.save()

                RadiusOrderProduct.objects.filter(order=order).delete()

                products_to_create = []
                for p_item in item.get('order_products', []):
                    product_created_at = self._parse_created_at(p_item.get('created_at'))
                    products_to_create.append(
                        RadiusOrderProduct(
                            order=order,
                            name=p_item.get('name'),
                            quantity=p_item.get('quantity', 1),
                            created_at=product_created_at or order.created_at,
                        )
                    )

                if products_to_create:
                    RadiusOrderProduct.objects.bulk_create(products_to_create)

                imported_count += 1

        self.stdout.write(self.style.SUCCESS(f"Muvaffaqiyatli saqlandi/yangilandi: {imported_count} ta Radius order"))
