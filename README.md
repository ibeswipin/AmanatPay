# AmanatPay

Django-сервис рассрочки: мерчанты, клиенты, заказы, график платежей.

Проект подготовлен для **President Tech Award**

## Архитектура

```
apps/                домен: модели, админка, choices
  utils/models.py    BaseModel (uuid, created_at, updated_at)
  merchant/          Merchant — магазин-партнёр (ИНН, комиссия)
  client/            Client — покупатель (лимит, debt, available_limit)
  order/             Order + Payment, choices.OrderStatus
services/            бизнес-логика
  order.py           build_schedule(), cancel(), InstallmentError
api/                 HTTP-слой (DRF)
  routers.py         RouterClass: DefaultRouter в DEBUG, иначе SimpleRouter
  merchant|client|order/  serializers.py, views.py, urls.py, filters.py
  urls.py            подключает всё под /api/v1/
config/              settings, urls, wsgi
```

Правило слоёв: `api` → `services` → `apps`. Модели не импортируют `api`,
`services` не знает про request/response.

## Эндпоинты (`/api/v1/`)

| Метод | Путь | Что делает |
|---|---|---|
| GET/POST | `merchants/`, `clients/`, `orders/` | CRUD, поиск `?search=`, фильтры |
| POST | `orders/{uuid}/schedule/` | построить график (`first_due` опционально) |
| POST | `orders/{uuid}/cancel/` | отменить заказ без оплат |
| GET | `payments/?order__uuid=&is_paid=` | график платежей |
| POST | `payments/{uuid}/pay/` | отметить платёж оплаченным |

## Логика рассрочки

`Order.total` = сумма × (1 + наценка%). `build_schedule()` делит на `months`
равных платежей, остаток округления кладёт в первый — сумма графика точно
равна `total`. Проверяет лимит клиента. Последняя оплата закрывает заказ.
`Order.merchant_payout` = сумма − комиссия мерчанта.

## Запуск

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
.venv/bin/python manage.py test
```
