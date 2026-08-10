# AmanatPay — Order Service

Срез order-сервиса, извлечённый из основного бэкенда AmanatPay/OmonatPay
для President Tech Award. Это код-витрина: показывает доменную модель,
слой сервисов и HTTP API заказов. Проект не самодостаточный — `config/`,
`manage.py` и смежные приложения (`user`, `company`, `merchandise`,
`wallet`, `delivery`, `marketplace`, `installment`) не входят в срез,
поэтому импорты на них не резолвятся.

## Структура

```
apps/order/          доменные модели, статусы, миграции, admin, signals
apps/utils/          общая инфраструктура: базовые модели, пермишены,
                     пагинация, ответы, миксины, валидаторы
api/order/           DRF-слой: views, serializers, filters, urls, routers
services/order.py    бизнес-логика заказов (~3.2k строк)
```

## Ключевые части

- `apps/order/models.py` — `Order`, `OrderProduct`, `DraftOrder`, логи статусов
- `apps/order/choices.py` — статусы заказа и переходы
- `services/order.py` — создание/подтверждение/отмена/возврат, расчёты,
  интеграции с рассрочкой, доставкой и кошельком
- `api/order/urls.py` — эндпоинты: CRUD, модерация, IMEI/маркировка,
  qabz (выдача), draft-order, накладные
