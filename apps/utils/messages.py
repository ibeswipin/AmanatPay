class SMSMessages:
    OTP_LOGIN = (
        "AmanatPay ilovasida kod: {code} ni kiritish orqali "
        "siz ofertadagi shartlarga rozilik bildirasiz: https://amanatpay.uz/public-offer/ "
        "{code_hash}"
    )

    OTP_RESET_PASSWORD = (
        "AmanatPay ilovasida parolni tiklash uchun kod: {code}. Agar bu siz bo'lmasangiz, zudlik "
        "bilan bizga xabar bering: https://amanatpay.uz/"
    )

    SELLER_CREDENTIALS = (
        "AmanatPay tizimiga kirish uchun Login: {username}, Parol: {password}"
    )

    # Qarzdorlik eslatmalari

    DEBT_LEGAL_ACTION = (
        "DIQQAT! {order_number} dan {amount} UZS qarzdorlik mavjud. "
        "Qarzingiz SUD va MIBga oshirilmoqda. AmanatPay yuridik bo'limi! Batafsil: 712003100"
    )

    DEBT_REMINDER_TOMORROW = (
        "HURMATLI MIJOZ! AmanatPay dan {due_date}da {order_number} bo'yicha {amount} "
        "UZS to'lovingiz. Juda ko'p vaqtida to'lovni amalga oshirishingizni so'raymiz. "
        "Batafsil: 712003100"
    )

    DEBT_OVERDUE_WARNING = (
        "Sizda AmanatPay dan {due_date}da {order_number} bo'yicha {amount} "
        "UZS muddati o'tgan qarzdorlikni sudlik bilan tolashingizni soraymiz. "
        "Batafsil: 712003100"
    )

    @classmethod
    def format(cls, template: str, **kwargs) -> str:
        """
        Args:
            template: Shablon matni
            **kwargs: Shablon parametrlari

        Returns:
            str: Formatlangan xabar

        Example:
            message = SMSMessages.format(
                SMSMessages.OTP_LOGIN,
                code="1234",
            )
        """
        return template.format(**kwargs)
