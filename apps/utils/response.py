from collections import OrderedDict

from rest_framework.response import Response as BaseResponse
from rest_framework_simplejwt.tokens import RefreshToken

from apps.user.models import Role, User
from apps.utils.functions import generate_dict


def get_user_token_data(user: User, role: Role, payload: dict = None) -> dict:
    refresh = RefreshToken.for_user(user)
    role.is_changed = False
    role.save()
    if payload:
        for key, value in payload.items():
            refresh[key] = value

    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "access_control": generate_dict(role.pk),
        "must_change_password": user.must_change_password,
    }


def get_user_token_data_from_refresh_token(refresh_token: str) -> dict:
    refresh = RefreshToken(refresh_token)
    user_id = refresh["user_id"]
    user = User.objects.get(id=user_id)
    return get_user_token_data(user)


def get_json_data(
    success: bool = True, error=None, message: str = None, data=None, *args, **kwargs
):
    res = OrderedDict(
        [
            ("success", success),
            ("error", error),
            ("message", message),
        ]
    )
    if kwargs:
        res.update(kwargs)

    res["data"] = data
    return res


class Response(BaseResponse):
    def __init__(
        self,
        data=None,
        status=None,
        template_name=None,
        headers=None,
        exception=False,
        content_type=None,
        success: bool = True,
        error=None,
        message: str = None,
        **kwargs
    ):
        json_data = get_json_data(success, error, message, data, **kwargs)
        super().__init__(
            json_data, status, template_name, headers, exception, content_type
        )
