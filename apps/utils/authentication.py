import jwt
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.user.models import Client, User


class UserJWTAuthentication(JWTAuthentication):
    PASSWORD_CHANGE_ALLOWED_PATHS = {
        "/api/user/change-password/",
    }

    def authenticate(self, request):
        raw_request = getattr(request, "_request", request)
        if (
                hasattr(raw_request, "_jwt_token_auth")
                and raw_request.user.is_authenticated
        ):
            return raw_request.user, raw_request._jwt_token_auth

        user_and_token = super().authenticate(request)
        if not user_and_token:
            return None

        user, token = user_and_token
        if (
                getattr(user, "must_change_password", False)
                and request.path not in self.PASSWORD_CHANGE_ALLOWED_PATHS
        ):
            raise AuthenticationFailed(_("Parolni yangilashingiz shart"))

        return user, token

    def get_user(self, validated_token):
        user_id = validated_token.get("user_id")
        try:
            return User.objects.get(id=user_id, is_active=True)
        except User.DoesNotExist:
            return None


# drf-spectacular uchun extension
class UserJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """
    OpenAPI schema extension for UserJWTAuthentication
    """

    target_class = "apps.utils.authentication.UserJWTAuthentication"
    name = "jwtAuth"
    priority = 1

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "JWT token autentifikatsiyasi.\n\n"
                "Token olish uchun `/api/auth/login/` endpointiga so'rov yuboring.\n\n"
                "Header formatі: `Authorization: Bearer <token>`"
            ),
        }


def authenticate(username, password) -> User | None:
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return None

    if not user.check_password(password):
        return None

    if not user.is_active:
        return None

    return user


class ClientTokenAuthentication(BaseAuthentication):
    """
    Client uchun JWT token authentication.

    Header: Authorization: Client {jwt_token}

    Token payload:
    {
      "token_type": "client",
      "exp": 1794295400,
      "iat": 1768375400,
      "jti": "25ab17ae547945d6acd918d22837f9b6",
      "client_id": "1",
    }

    Usage in views:
        request.user  # Client instance
        request.auth  # JWT token string
    """
    keyword = 'Client'

    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        if not auth_header.startswith(f'{self.keyword} '):
            return None

        token = auth_header[len(f'{self.keyword} '):]  # noqa

        if not token:
            return None

        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=['HS256']
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token muddati tugagan')
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed(f'Noto\'g\'ri token: {str(e)}')

        token_type = payload.get('token_type')
        if token_type != 'client':
            raise AuthenticationFailed('Noto\'g\'ri token turi')

        client_id = payload.get('client_id')
        if not client_id:
            raise AuthenticationFailed('Token da client_id yo\'q')

        client = Client.objects.filter(id=client_id).first()
        if not client:
            raise AuthenticationFailed('Mijoz topilmadi')

        # # this field is required by SimpleJWTAuthentication
        # client.is_authenticated = True
        # client.is_anonymous = False

        return (client, token)

    def authenticate_header(self, request):
        """401 response uchun WWW-Authenticate header"""
        return self.keyword
