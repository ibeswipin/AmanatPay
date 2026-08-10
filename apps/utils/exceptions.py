from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException


class CustomException(APIException):
    default_code = "error"
    status_code = 400
    default_detail = _("Xatolik yuz berdi!")

    def __init__(self, detail=None, code=None, status_code=None):
        self.code = code if code else self.default_code
        self.status_code = status_code if status_code else self.status_code
        self.detail = detail if detail else self.default_detail


class RoleNotChosenException(CustomException):
    default_code = "need_to_role_choice"
    status_code = 444
    default_detail = _("Rol tanlanmagan!")


class MissingGroupPermissionException(CustomException):
    default_code = "need_to_set_group"
    status_code = 403
    default_detail = _("Rol uchun ruxsatlar guruhini belgilashingiz kerak!")


class PermissionDeniedException(CustomException):
    default_code = "permission_denied"
    status_code = 403
    default_detail = _("Huquqingiz yetarli emas!")


class NotFoundException(CustomException):
    default_code = "not_found"
    status_code = 404
    default_detail = _("Ma'lumot topilmadi!")


class BadRequestException(CustomException):
    default_code = "bad_request"
    status_code = 400
    default_detail = _("So'rov noto'g'ri!")


class NotAuthenticatedException(CustomException):
    default_code = "not_authenticated"
    status_code = 401
    default_detail = _("Kirish mumkin emas!")
