from datetime import date

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.utils.exceptions import CustomException

INVALID_CARDS_LIST = [
    # Avvalgi ro'yxatdagilar
    "98600102", "98600202", "98600302", "98600304", "98600305",
    "98600322", "98600323", "98600330", "98600335", "98600367",
    "98600402", "98600403", "98600404", "98600432", "98600602",
    "98600603", "98600607", "98600610", "98600613", "98600802",
    "98600804", "98600815", "98600816", "98600817", "98600818",
    "98600826", "98600827", "98600828", "98600829", "98600902",
    "98601002", "98601005", "98601006", "98601202", "98601203",
    "98601205", "98601232", "98601302", "98601402", "98601403",
    "98601404", "98601502", "98601504", "98601602", "98601603",
    "98601702", "98601802", "98601807", "98601902", "98601903",
    "98601905", "98602002", "98602102", "98602103", "98602104",
    "98602302", "98602303", "98602402", "98602502", "98602602",
    "98602603", "98602604", "98602702", "98602703", "98602802",
    "98602804", "98602902", "98602903", "98603002", "98603102",
    "98603202", "98603302", "98603402", "98603502", "98603702",
    "98606002", "98606003", "98606067", "98606068",
    # Yangi qo'shilganlar
    "544081002", "544081004", "544081147", "561468031", "56146804110",
    "56146804111", "561468057", "5614680571", "5614680572", "5614680670",
    "5614680671", "561468071", "561468073", "5614680820", "5614680821",
    "561468092", "561468102", "561468103", "56146810799", "561468123",
    "561468124", "5614681252", "561468141", "5614681430", "5614681433",
    "561468164", "561468165", "561468172", "5614681820", "5614681821",
    "561468203", "5614682121", "5614682122", "5614682124", "5614682270",
    "5614682271", "5614682470", "5614682471", "561468253", "561468254",
    "5614682670", "5614682674", "5614682675", "5614682770", "5614682771",
    "5614682870", "5614682871", "5614682872", "5614682873", "5614682970",
    "5614682971", "561468302", "5614683070", "56146830710", "5614683570",
    "561468385", "5614683870", "5614683871", "5614688570", "5614688571",
    "5614688670", "5614688671", "5614688870", "5614688871", "62624801003",
    "62624801010", "626253091", "626253092", "626257010", "626257020",
    "62627202", "62628204", "62629110", "62629111", "62629201",
    "62641803", "62641804", "62642010", "62642504", "62642505",
    "62642509", "62642511", "62642512", "62642516", "62642521",
    "62642522", "626425320", "626425321"
]
def file_size_validator(value):  # add this to some file where you can import it from
    limit = 1024 * 1024
    if value.size > limit:
        raise CustomException(detail=_("Faylning hajmi 1 MB dan oshmasligi kerak."))

def phone_validator(value):
    format_text = _("Telefon raqami quyidagi formatda bo'lishi kerak: 998XXXXXXXXX")
    if not value.isdigit():
        raise CustomException(detail=_("Telefon raqami faqat raqamlardan iborat bo'lishi kerak. %(format)s")
            % {"format": format_text}
        )
    elif len(value) != 12:
        raise CustomException(detail=_("Telefon raqami 12 ta raqamdan iborat bo'lishi kerak. %(format)s")
            % {"format": format_text}
        )
    elif not value.startswith("998"):
        raise CustomException(detail=_("Telefon raqami (998) bilan boshlanishi kerak. %(format)s")
            % {"format": format_text}
        )
    return value


def date_of_birth_validator(value):
    if value > date.today():
        raise CustomException(detail=_("Tug'ilgan sana bugungi kundan katta bo'lishi mumkin emas.")
        )
    elif value.year < 1900:
        raise CustomException(detail=_("Tug'ilgan sana 1900 yildan kichik bo'lishi mumkin emas.")
        )


def validate_ids(data, field="external_id", unique=True):
    if isinstance(data, list):
        id_list = [x[field] for x in data]

        if unique and len(id_list) != len(set(id_list)):
            raise CustomException(detail=_("Bir xil %(field)s uchun bir nechta yangilanish topildi")
                % {"field": field}
            )

        return id_list

    return [data]


def card_number_validator(value):
    format_text = _("Karta raqami quyidagi formatda bo'lishi kerak: 8600XXXXXXXXXXXX")
    if not value.isdigit():
        raise CustomException(detail=_("Karta raqami faqat raqamlardan iborat bo'lishi kerak. %(format)s")
            % {"format": format_text}
        )
    elif len(value) != 16:
        raise CustomException(detail=_("Karta raqami 16 ta raqamdan iborat bo'lishi kerak. %(format)s")
            % {"format": format_text}
        )
    elif value[:8] in set(INVALID_CARDS_LIST):
        raise CustomException(detail=_("Pensiya, Korparativ, YATT va boshqa ijtimoiy himoyalash kartalari qabul qilinmaydi")
        )
    elif value[:9] in set(INVALID_CARDS_LIST):
        raise CustomException(detail=_("Pensiya, Korparativ, YATT va boshqa ijtimoiy himoyalash kartalari qabul qilinmaydi")
        )
    elif value[:10] in set(INVALID_CARDS_LIST):
        raise CustomException(detail=_("Pensiya, Korparativ, YATT va boshqa ijtimoiy himoyalash kartalari qabul qilinmaydi")
        )
    elif value[:11] in set(INVALID_CARDS_LIST):
        raise CustomException(detail=_("Pensiya, Korparativ, YATT va boshqa ijtimoiy himoyalash kartalari qabul qilinmaydi")
        )
    elif value[:12] in set(INVALID_CARDS_LIST):
        raise ValidationError(
            _("Pensiya, Korparativ, YATT va boshqa ijtimoiy himoyalash kartalari qabul qilinmaydi")
        )


def expire_date_validator(value):
    format_text = _(
        "Kartaning amal qilish muddati quyidagi formatda bo'lishi kerak: MMYY"
    )
    if not value.isdigit():
        raise CustomException(detail=_(
                "Kartaning amal qilish muddati faqat raqamlardan iborat bo'lishi kerak. %(format)s"
            )
            % {"format": format_text}
        )
    elif len(value) != 4:
        raise CustomException(detail=_(
                "Kartaning amal qilish muddati 4 ta raqamdan iborat bo'lishi kerak. %(format)s"
            )
            % {"format": format_text}
        )


def validate_otp(value, length: int = 4):
    if not value.isdigit():
        raise CustomException(detail=_("Tasdiqlash kodi faqat raqamlardan iborat bo'lishi kerak.")
        )
    elif len(value) != length:
        raise CustomException(detail=_("Tasdiqlash kodi %(length)s ta raqamdan iborat bo'lishi kerak.")
            % {"length": length}
        )


def file_size_validator(value):
    limit = 1024 * 1024
    if value.size > limit:
        raise CustomException(detail=_("Fayl hajmi juda katta. Hajmi 1 MB dan oshmasligi kerak.")
        )


def stir_validator(value):
    if not value.isdigit():
        raise CustomException(detail=_("STIR faqat raqamlardan iborat bo'lishi kerak."))
    elif len(value) != 9:
        raise CustomException(detail=_("STIR 9 ta raqamdan iborat bo'lishi kerak."))


def imei_validator(value):
    """IMEI (International Mobile Equipment Identity) validator."""
    if len(value) != 15 or not value.isdigit():
        raise CustomException(detail="Invalid IMEI")

    # total = sum(
    #     (
    #         (_ := int(d)) * 2 - 9
    #         if i % 2 and (_ := int(d)) > 4
    #         else (_ := int(d)) * 2 if i % 2 else int(d)
    #     )
    #     for i, d in enumerate(reversed(value))
    # )
    #
    # if total % 10:
    #     raise ValidationError("Invalid IMEI")

    return value


def marking_validator(value: str):
    """Validator for product markings."""
    if len(value) != 38:
        raise CustomException(detail="Markirovka uzunligi noto'g'ri")
    if not value.startswith("01"):
        raise CustomException(detail="Markirovka prefiksi noto'g'ri")

    return value


def ikpu_validator(value: str):
    """Validator for product ikpu """
    if len(value) != 17 or not value.isdigit():
        raise CustomException(detail="Ikpu 17 ta raqamdan iborat bo'lishi kerak")
    return value


def first_payment_validator(value):
    """Validator for first payment."""
    if value is None:
        return None

    today = date.today()
    max_date = today + relativedelta(months=1)

    if value < today:
        raise CustomException(detail=_("Birinchi to'lov sanasi bugundan orqada bo'lishi mumkin emas")
        )
    if value > max_date:
        raise CustomException(detail=_("Birinchi to'lov sanasi 1 oydan oshib ketmasligi kerak")
        )

    return value


def validate_scheduled_end(value):
    now = timezone.now()

    if value < now:
        raise CustomException(detail=_("Rejalashtirilgan vaqt hozirgi vaqtdan keyingi bo'lishi kerak.")
        )
    return value
