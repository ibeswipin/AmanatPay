from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderStatusChoices(models.TextChoices):
    DRAFT = "draft", _("Buyurtma yaratildi")

    PENDING_VERIFICATION = "pending_verification", _("Tekshiruv kutilmoqda")
    PENDING_PARTNER = "pending_partner", _("Hamkor kutilmoqda")

    QABZ_PENDING = "qabz_pending", _("Qabz kutilmoqda")
    QABZ_COMPLETED = "qabz_completed", _("Qabz")

    ON_WAY = "on_way", _("Yo'lda")
    DELIVERED = "delivered", _("Yetkazib berildi")

    CANCELLED_MERCHANT = "cancelled_merchant", _("Bekor qilindi (Merchant)")
    CANCELLED_AMANAT = "cancelled_amanat", _("Bekor qilindi (Amanat)")
    RETURN_PENDING_CLIENT = "return_pending_client", _("Mijoz tasdig'i kutilmoqda(return)")
    RETURN_PENDING_MERCHANT = "return_pending_merchant", _("Merchant tasdig'i kutilmoqda(return)")
    RETURNED = "returned", _("Qaytarildi")

    ACTIVE = "active", _("Faol")
    CLOSED = "closed", _("Yopildi")


class OrderSourceChoices(models.TextChoices):
    ONLINE = "online", _("Online")
    OFFLINE = "offline", _("Offline")
    MARKETPLACE = "marketplace", _("Marketplace")


class OrderCreationTypeChoices(models.TextChoices):
    """Buyurtma qaysi yo'l bilan yaratilgani.

    `source` kanalni bildiradi (online/offline filial yoki marketplace),
    bu esa boshqa o'q — buyurtmani merchant o'zi kiritdimi yoki mijoz
    ilova orqali yaratdimi.
    """

    MANUALLY = "manually", _("Qo'lda (merchant)")
    MOBILE = "mobile", _("Mobil ilova")


class PaymentFactStatusChoices(models.TextChoices):
    PAID = "paid", _("To'langan")
    OVERDUE = "overdue", _("Muddati o'tgan")
    PENDING = "pending", _("Kutilmoqda")
    EXTRA = "extra", _("Qo'shimcha to'lov")
    PARTIALLY_PAID = "partially_paid", _("Qisman to'langan")


class TransactionSourceChoices(models.TextChoices):
    PAYME = "payme", _("Payme")
    CLICK = "click", _("Click")
    PAYNET = "paynet", _("Paynet")
    UZUM = "uzum", _("Uzum")
    PLUM = "plum", _("Plum")
    CASH = "cash", _("Naqd pul")
    PRIVATE_BALANCE = "private_balance", _("Shaxsiy balans")
    CARD = "card", _("Karta")
    AUTO = "auto", _("Avto to'lov")
    TRANSFER = "transfer", _("Bank o'tkazmasi")
    OTHER = "other", _("Boshqa")


class MerchantPaymentTypeChoices(models.TextChoices):
    PAYMENT = "payment", _("To'lov")
    REFUND = "refund", _("Qaytarish")


class MerchantPaymentStatusChoices(models.TextChoices):
    PENDING = "pending", _("Kutilmoqda")
    PAID = "paid", _("To'landi")
    CANCELLED = "cancelled", _("Bekor qilindi")
    REFUNDED = "refunded", _("Bekor qilindi")


class ExportStatusChoices(models.TextChoices):
    PENDING = "pending", _("Kutilmoqda")
    PROCESSING = "processing", _("Jarayonda")
    SUCCESS = "success", _("Muvaffaqiyatli yakunlandi")
    FAILED = "failed", _("Xatolik")


class ExportTypeChoices(models.TextChoices):
    MERCHANT_PAYMENT = "merchant_payment", _("Merchant payment")
    TRANSACTION = "transaction", _("Tranzaksiya")
    ORDER_REPORT = "order_report", _("Shartnomalar")


class AdditionalServiceTypeChoices(models.TextChoices):
    DELIVERY_BTS = "delivery_bts", _("Yetkazib berish (BTS)")
    DELIVERY_NESUVEZU = "delivery_nesuvezu", _("Yetkazib berish (NesuVezu)")


class ReceiptImageTypeChoices(models.TextChoices):
    RECEIVED = "received", _("Qabul qilindi")
    HANDED_OVER = "handed_over", _("Topshirildi")


class PartnerPayment(models.TextChoices):
    radius = "radius", "Radius"
    amanat = "amanat", "Amanat"


class RadiusOrderTypeChoices(models.TextChoices):
    PURCHASE = "purchase", _("Xarid (Покупка)")
    INSTALLMENT = "installment", _("Muddatli to'lov (Рассрочка)")


class RadiusOrderStatusChoices(models.TextChoices):
    NEW = "new", _("Yangi (Новый)")
    UNCONFIRMED = "unconfirmed", _("Tasdiqlanmagan (Не подтверждено)")
    CONFIRMED = "confirmed", _("Tasdiqlangan (Подтвержденный)")
    DONE = "done", _("Sotilgan (Продан)")
    CANCELED = "canceled", _("Bekor qilingan (Отменен)")
    RETURNED = "returned", _("Qaytarilgan (Возвращен)")
    CLOSED = "closed", _("Yopilgan (Закрыт)")
