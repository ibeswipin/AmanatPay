import asyncio
import base64
import datetime
from typing import Optional

import jwt
import pytz
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import caches

from apps.delivery.choices import ExternalSystemChoices
from apps.user.models import Company, Role
from apps.utils.exceptions import MissingGroupPermissionException, RoleNotChosenException


def get_radius_company():
    return Company.objects.filter(tin="305379480").first()


def revert_value(value: str):
    type_mapping = {
        "int": int,
        "float": float,
        "str": str,
        "bool": lambda x: x.lower()
        in ["true", "1", "yes"],  # Handling common true values
    }

    if "__revert_to__" in value:
        val, type_str = value.split("__revert_to__")
        val = val.strip()
        type_str = type_str.strip()

        if type_str in type_mapping:
            return type_mapping[type_str](val)
        else:
            raise ValueError(f"Unsupported type: {type_str}")
    if value in ["true", "false", "True", "False"]:
        value = eval(value.capitalize())
    return value


def decode_token(request) -> dict:
    """
    Decode token from request
    :param request: Request
    :return
    :dict
    """
    token = request.META.get("HTTP_AUTHORIZATION", "")
    if token:
        token = token.split(" ")[-1]
        return jwt.decode(token, options={"verify_signature": False})
    return {}


def validate_kwargs_string(kwargs_string: str):
    """
    Validate the kwargs string and return a dictionary of key-value pairs.
    Example:
        "key1=value1,key2=value2" -> {"key1": "value1", "key2": "value2"}
        "key1=value1, key2=value2" -> {"key1": "value1", "key2": "value2"}
    """
    if not kwargs_string:
        return {}

    _filter = {}
    for item in kwargs_string.split(","):
        key, value = item.split("=")
        key = key.strip()
        value = value.strip()

        if key.endswith("__in"):
            values = value.split(" ")
            _filter[key] = [revert_value(v) for v in values]
        else:
            _filter[key] = revert_value(value)
    return _filter


#  api permission functions
def build_nested_dict(matrix):
    root = {}

    for row in matrix:
        current = root
        for num in reversed(row):  # Reverse the row before processing
            current = current.setdefault(num, {})
    return root


def generate_dict(role_id: int):
    group = Role.objects.get(id=role_id).group
    if not group:
        raise MissingGroupPermissionException()
    user_permissions = (
        Role.objects.get(id=role_id).permissions.select_related("module").all()
    )
    group_permissions = group.permissions.select_related("module").all()
    exclude_permission_ids = Role.objects.get(
        id=role_id
    ).exclude_permissions.values_list("id", flat=True)

    # Filter out excluded permissions before union
    filtered_group_permissions = group_permissions.exclude(
        id__in=exclude_permission_ids
    )
    filtered_user_permissions = user_permissions.exclude(id__in=exclude_permission_ids)

    permissions = filtered_group_permissions.union(filtered_user_permissions)

    perm_dict = {"modules": {}, "permissions": {}}
    perm_matrix = []
    for perm in permissions:
        if perm.module and perm.module.hierarchy:
            perm_matrix.append(perm.module.hierarchy[:-1].split(":"))
        else:
            perm_matrix.append([])
        perm_dict["permissions"][perm.name] = True

    perm_dict["modules"] = build_nested_dict(perm_matrix)
    caches[settings.REDIS_CACHE_KEY].set(
        f"rol_{role_id}", perm_dict, settings.REDIS_CACHE_TIMEOUT
    )
    return perm_dict


def get_perm_dict(role_id: int):
    perm_dict = caches[settings.REDIS_CACHE_KEY].get(f"rol_{role_id}")

    if not perm_dict:
        raise RoleNotChosenException()
    return perm_dict

async def get_perm_dict_async(role_id: int):
    perm_dict = await asyncio.to_thread(
        caches[settings.REDIS_CACHE_KEY].get,
        f"rol_{role_id}"
    )

    if not perm_dict:
        return False
    return perm_dict


def remove_perm_dict(role_id: int):
    caches[settings.REDIS_CACHE_KEY].delete(f"rol_{role_id}")


def has_perm_dict(role_id: int):
    return caches[settings.REDIS_CACHE_KEY].get(f"rol_{role_id}")


def get_tashkent_time(time_):
    tashkent_timezone = pytz.timezone("Asia/Tashkent")
    return time_.astimezone(tashkent_timezone)


def convert_from_utc_to_tashkent_date(iso_string):
    if "+" in iso_string and iso_string[-5] == "+":
        iso_string = iso_string[:-2] + ":" + iso_string[-2:]
    datetime_obj_utc = datetime.datetime.fromisoformat(iso_string)
    return get_tashkent_time(datetime_obj_utc)


def base64_2_text(base64_arg):
    return base64.b64decode(base64_arg).decode("utf-8")


def start_report_decode(data):
    data = base64_2_text(data)
    json = {"creditor": False}
    soup = BeautifulSoup(data, "html.parser")
    text = soup.select("table")
    info = {}
    for i in text[0].select("tr")[1:]:
        t = i.select("td")[1:]
        info[t[0].getText()] = {
            "requests": int(t[1].getText()),
            "refuse": int(t[2].getText()),
            "orders": int(t[3].getText()),
            "not_solution": int(t[4].getText()),
        }
    json["report"] = info
    if len(text) > 1:
        info = {}
        for s, i in enumerate(text[1].select("tr")[1:]):
            t = i.select("td")[1:]
            info[s] = {
                "organization": t[0].getText(),
                "order": t[1].getText(),
                "currency": t[2].getText(),
                "debt": int(t[3].getText().replace(" ", "")),
                "delay": int(t[4].getText().replace(" ", "")),
                "average_payment": int(t[5].getText().replace(" ", "")),
            }
        json["creditor"] = info
    return json


def inps_report_decode(data):
    data = base64_2_text(data)
    json = {"type": False, "report": False}
    soup = BeautifulSoup(data, "html.parser")
    text = soup.select("table")
    if len(text) == 1:
        info = {}
        for i in text[0].select("tr")[1:]:
            t = i.select("td")[1:]
            info[t[0].getText()] = {
                "result": t[1].getText(),
            }
        json["type"] = info
    elif len(text) > 1:
        info = {}
        for s, i in enumerate(text[0].select("tr")[1:]):
            t = i.select("td")[1:]
            if len(t) < 3:
                continue
            try:
                info[s] = {
                    "accrual_date": t[0].getText(),
                    "organization": t[1].getText(),
                    "stir": t[3].getText(),
                    "income_date": t[4].getText(),
                    "salary": float(t[6].getText().replace(" ", "")),
                }
                try:
                    info[s].update({"summa_insp": float(t[5].getText().replace(" ", ""))})
                except:  # noqa
                    info[s].update({"summa_insp": t[5].getText().replace(" ", "")})
            except:  # noqa
                info[s] = {
                    "accrual_date": t[0].getText(),
                    "organization": "",
                    "stir": "",
                    "income_date": "",
                    "salary": float(t[3].getText().replace(" ", "")),
                }
                try:
                    info[s].update({"summa_insp": float(t[2].getText().replace(" ", ""))})
                except:  # noqa
                    info[s].update({"summa_insp": t[2].getText().replace(" ", "")})  # noqa
        json["report"] = info
        info = {}
        for i in text[-1].select("tr")[1:]:
            t = i.select("td")[1:]
            info[t[0].getText()] = {
                "result": t[1].getText(),
            }
        json["type"] = info
    return json


def format_amount(amount) -> str:
    """Summani formatlash: 1 234 567"""
    return f"{amount:,.0f}".replace(",", " ")  # noqa


def amount_to_words_uz(amount) -> str:
    """
    Summani o'zbek tilida so'z bilan yozish.
    Masalan: 2700000 -> "ikki million yetti yuz ming"
    """
    amount = int(amount)

    if amount == 0:
        return "nol"

    ones = [
        "", "bir", "ikki", "uch", "to'rt", "besh",
        "olti", "yetti", "sakkiz", "to'qqiz",
    ]
    tens = [
        "", "o'n", "yigirma", "o'ttiz", "qirq", "ellik",
        "oltmish", "yetmish", "sakson", "to'qson",
    ]
    scales = [
        (1_000_000_000_000, "trillion"),
        (1_000_000_000, "milliard"),
        (1_000_000, "million"),
        (1_000, "ming"),
    ]

    if amount < 0:
        return "minus " + amount_to_words_uz(-amount)

    parts = []

    for value, name in scales:
        if amount >= value:
            count = amount // value
            amount %= value
            parts.append(_three_digits_uz(count, ones, tens) + " " + name)

    if amount >= 100:
        parts.append(ones[amount // 100] + " yuz")
        amount %= 100

    if amount >= 10:
        parts.append(tens[amount // 10])
        amount %= 10

    if amount > 0:
        parts.append(ones[amount])

    return " ".join(parts).strip()


def _three_digits_uz(n: int, ones: list, tens: list) -> str:
    """Uch xonali sonni so'zga aylantirish (ichki yordamchi)."""
    result = []
    if n >= 100:
        result.append(ones[n // 100] + " yuz")
        n %= 100
    if n >= 10:
        result.append(tens[n // 10])
        n %= 10
    if n > 0:
        result.append(ones[n])
    return " ".join(result)


def amount_to_words_ru(amount) -> str:
    """
    Сумма прописью на русском языке.
    Например: 2700000 -> "два миллиона семьсот тысяч"
    """
    amount = int(amount)

    if amount == 0:
        return "ноль"

    if amount < 0:
        return "минус " + amount_to_words_ru(-amount)

    ones_m = [
        "", "один", "два", "три", "четыре", "пять",
        "шесть", "семь", "восемь", "девять",
    ]
    ones_f = [
        "", "одна", "две", "три", "четыре", "пять",
        "шесть", "семь", "восемь", "девять",
    ]
    teens = [
        "десять", "одиннадцать", "двенадцать", "тринадцать",
        "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать",
        "восемнадцать", "девятнадцать",
    ]
    tens = [
        "", "десять", "двадцать", "тридцать", "сорок", "пятьдесят",
        "шестьдесят", "семьдесят", "восемьдесят", "девяносто",
    ]
    hundreds = [
        "", "сто", "двести", "триста", "четыреста", "пятьсот",
        "шестьсот", "семьсот", "восемьсот", "девятьсот",
    ]

    # (value, singular, few, many, feminine)
    scales = [
        (1_000_000_000_000, "триллион", "триллиона", "триллионов", False),
        (1_000_000_000, "миллиард", "миллиарда", "миллиардов", False),
        (1_000_000, "миллион", "миллиона", "миллионов", False),
        (1_000, "тысяча", "тысячи", "тысяч", True),
    ]

    parts = []

    for value, s1, s2_4, s5, feminine in scales:
        if amount >= value:
            count = amount // value
            amount %= value
            chunk = _three_digits_ru(count, ones_f if feminine else ones_m, teens, tens, hundreds)
            parts.append(chunk + " " + _ru_plural(count, s1, s2_4, s5))

    # Сотни, десятки, единицы
    chunk = _three_digits_ru(amount, ones_m, teens, tens, hundreds)
    if chunk:
        parts.append(chunk)

    return " ".join(parts).strip()


def _three_digits_ru(n: int, ones: list, teens: list, tens: list, hundreds: list) -> str:
    """Три цифры прописью (вспомогательная)."""
    if n == 0:
        return ""
    result = []
    if n >= 100:
        result.append(hundreds[n // 100])
        n %= 100
    if 10 <= n <= 19:
        result.append(teens[n - 10])
        return " ".join(result)
    if n >= 10:
        result.append(tens[n // 10])
        n %= 10
    if n > 0:
        result.append(ones[n])
    return " ".join(result)


def _ru_plural(n: int, form1: str, form2_4: str, form5: str) -> str:
    """Склонение русских существительных по числу."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return form5
    last = n % 10
    if last == 1:
        return form1
    if 2 <= last <= 4:
        return form2_4
    return form5


def get_data_from_mrz(mrz: str):
    if mrz.startswith("IUUZ"):
        data = {"doc_series": mrz[5:7], "doc_number": mrz[7:14], "pinfl": mrz[15:29]}
    else:
        data = {"doc_series": mrz[44:46], "doc_number": mrz[46:53], "pinfl": mrz[72:86]}
    return data


def char_to_value(c):
    if c.isdigit():
        return int(c)
    elif c.isalpha():
        return ord(c.upper()) - 55  # A=10, B=11, ..., Z=35
    elif c == "<":
        return 0
    else:
        raise ValueError(f"Invalid character: {c}")


def compute_check_digit(data):
    weights = [7, 3, 1]
    total = 0
    for i, c in enumerate(data):
        total += char_to_value(c) * weights[i % 3]
    return str(total % 10)


def build_passport_mrz(
        passport_number,
        surname,
        given_name,
        birth_date,
        gender,
        expiry_date,
        pinfl,
        country_code="UZB",
):
    line1 = f"P<{country_code}{surname.upper()}<<{given_name.upper()}"
    line1 = line1.replace("'", "").replace("`", "").replace(" ", "<").ljust(44, "<")[:44]

    passport_cd = compute_check_digit(passport_number)
    birth_cd = compute_check_digit(birth_date)
    expiry_cd = compute_check_digit(expiry_date)
    pinfl_cd = compute_check_digit(pinfl)

    composite = (
            passport_number
            + passport_cd
            + birth_date
            + birth_cd
            + expiry_date
            + expiry_cd
            + pinfl
            + pinfl_cd
    )
    final_cd = compute_check_digit(composite)

    line2 = (
        f"{passport_number}{passport_cd}"
        f"{country_code}"
        f"{birth_date}{birth_cd}"
        f"{gender.upper()}"
        f"{expiry_date}{expiry_cd}"
        f"{pinfl}{pinfl_cd}"
        f"{final_cd}"
    )
    line2 = line2.ljust(44, "<")[:44]

    return line1 + line2


def build_id_card_mrz(
        id_number,
        surname,
        given_name,
        birth_date,
        gender,
        expiry_date,
        pinfl,
        country_code="UZB",
):
    id_cd = compute_check_digit(id_number)
    line1 = f"IUUZB{id_number}{id_cd}{pinfl}<"
    line1 = line1.ljust(30, "<")[:30]

    birth_cd = compute_check_digit(birth_date)
    expiry_cd = compute_check_digit(expiry_date)
    double_country = "UZBUZB"

    line2_data = (
        f"{birth_date}{birth_cd}{gender}{expiry_date}{expiry_cd}{double_country}"
    )
    mrz_cd = compute_check_digit(line2_data)  # ✅ faqat line2_data bilan

    line2 = f"{line2_data}".ljust(29, "<") + mrz_cd
    line2 = line2[:30]  # shunchaki ehtiyot chorasi

    line3 = f"{surname.upper()}<<{given_name.upper()}"
    line3 = line3.replace("'", "").replace("`", "").replace(" ", "<").ljust(30, "<")[:30]

    return line1 + line2 + line3


def get_delivery_external_service_type(region) -> str:
    """
    Region SOATO kodiga qarab ExternalSystemChoices qiymatini (str) qaytaradi.

    - SOATO == '1726'  → 'nesuvezu'  (Toshkent shahri)
    - Boshqa holatlar  → 'bts'

    """
    soato = getattr(region, "soato", None)
    if soato == "1726":
        return ExternalSystemChoices.NESUVEZU
    return ExternalSystemChoices.BTS


def merchant_payload(company, request=None) -> Optional[dict]:
    """Mobil javoblar uchun merchant bloki: `{id, name, logo}`.

    Order list va detail bir xil shakl berishi uchun bitta joydan quriladi.
    Logo R2 da saqlangani uchun `.url` odatda to'liq URL qaytaradi; `request`
    berilsa lokal (nisbiy) yo'llar ham absolute qilinadi.
    """
    if company is None:
        return None

    logo = getattr(company, "logo", None)
    logo_url = None
    if logo:
        logo_url = logo.url
        if request is not None:
            logo_url = request.build_absolute_uri(logo_url)

    return {
        "id": company.id,
        "name": company.name,
        "logo": logo_url,
    }
