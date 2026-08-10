from string import ascii_letters, ascii_lowercase
from time import time_ns
from uuid import uuid4

from barcode import UPCA
from barcode.writer import ImageWriter
from django.utils.crypto import get_random_string
from faker import Faker


ALLOWED_NUMBERS = "1234567890"
ALLOWED_CHARS = ascii_lowercase


def generate_unique_code(length: int = 12) -> str:
    code = get_random_string(length=length, allowed_chars=ALLOWED_NUMBERS)
    if code.startswith("0"):
        return generate_unique_code(length=length)
    return code


def generate_password(length: int = 10) -> str:
    code = get_random_string(
        length=length, allowed_chars=ALLOWED_NUMBERS + ascii_letters
    )
    if code.startswith("0"):
        return generate_password(length=length)
    return code


def generate_unique_code_with_chars(length: int = 10) -> str:
    code = get_random_string(
        length=length, allowed_chars=ALLOWED_NUMBERS + ALLOWED_CHARS
    )
    if code.startswith("0"):
        return generate_unique_code_with_chars(length=length)
    return code


def gen_unique_cod():
    """Generate unique code. like 1690191397227819800d9d03cffab744127b835d4ce0c096d0f"""
    return f"{time_ns()}{str(uuid4()).replace('-', '')}"


def gen_uuid4_code():
    """
    Generate unique code. like 210e90d9-3619-49fe-9be6-072f8369b8bb
    """
    return uuid4().__str__()


def gen_unique_phone() -> str:
    """ "Generate unique phone. like 998931234567"""
    phone = f"9989{generate_unique_code(length=8)}"
    if len(phone) != 12:
        return gen_unique_phone()
    return phone


def gen_coupon():
    return generate_unique_code(length=12)


def generate_upc_code() -> str:
    original_code = generate_unique_code(12)
    code = UPCA(original_code).get_fullcode()
    return code


def gen_barcode(number: str):
    my_code = UPCA(number, writer=ImageWriter())
    file_name = "barcode"
    save_file = f"media/barcode/{file_name}"
    file_path = f"{save_file}.png"
    my_code.save(save_file)
    with open(file_path, "rb") as f:
        return f.read()


def generate_username():
    username = Faker().name_male().split(" ")[0].lower() + str(
        generate_unique_code(length=2)
    )
    return username


def generate_gmail():
    gmail = Faker().email()
    return gmail


def generate_url(length: int = 10) -> str:
    return get_random_string(length=length, allowed_chars=ascii_letters)
