from collections.abc import Mapping

import sentry_sdk
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response as DRFResponse

from apps.utils.permissions import ApiPermission, DenyAll
from apps.utils.response import Response


class ActionSerializerMixin(object):
    """
    Mixin for declaring per action serializers for Viewset.
    """

    ACTION_SERIALIZERS = {}

    def get_serializer_class(self):
        if self.action in self.ACTION_SERIALIZERS:
            return self.ACTION_SERIALIZERS[self.action]

        return super().get_serializer_class()

    def get_paginate_list(self, queryset):
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return serializer


class ActionPermissionMixin(object):
    """
    Mixin for declaring per action serializers for Viewset.
    """

    DEFAULT_PERMISSION_CLASS = ApiPermission
    ACTION_PERMISSIONS: dict = {}
    http_method_names = ["get", "post", "put", "delete"]

    def get_permissions(self):
        if self.action in self.ACTION_PERMISSIONS:
            return [permission() for permission in self.ACTION_PERMISSIONS[self.action]]

        return [self.DEFAULT_PERMISSION_CLASS()]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.get_queryset_by_action(queryset)
        queryset = self.filter_by_role(queryset)
        return queryset

    def get_queryset_by_action(self, queryset):
        return queryset

    def filter_by_role(self, queryset):
        return queryset


class ActionMobilePermissionMixin(object):
    """
    Mixin for declaring per action serializers for Viewset.
    """

    DEFAULT_PERMISSION_CLASS = DenyAll
    ACTION_PERMISSIONS: dict = {}
    http_method_names = ["get", "post", "put", "delete"]

    def get_permissions(self):
        if self.action in self.ACTION_PERMISSIONS:
            return [permission() for permission in self.ACTION_PERMISSIONS[self.action]]

        return [self.DEFAULT_PERMISSION_CLASS()]

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = self.filter_by_role(queryset)
        return queryset

    def filter_by_role(self, queryset):
        return queryset


class HandleExceptionMixin(object):
    """
    Mixin for handling exceptions.
    """

    def handle_exception(self, exc, context=None):
        message = None
        error = str(exc)

        if isinstance(exc, ValidationError):
            error = self.validation_error_type(exc)

        if isinstance(exc, Http404):
            message = getattr(exc, "message", None) or _("Ma'lumot topilmadi!")
            return Response(
                status=404,
                message=message,
                success=False,
            )

        if getattr(exc, "detail", None):
            message = exc.detail
            if isinstance(exc.detail, dict):
                message = exc.detail.get("detail") or exc.detail.get("message")
        elif getattr(exc, "message", None):
            message = exc.message

        status_code = getattr(exc, "status_code", 500)
        if status_code >= 500:
            sentry_sdk.capture_exception(exc)

        return Response(
            error=error if not message else None,
            status=status_code,
            message=message,
            success=False,
        )

    def validation_error_type(self, exc: ValidationError):
        """
        Example:
            {'birth_date': [ErrorDetail(...)]} → {'birth_date': ['Yaroqli tug\'ilgan sana kiriting']}
        """
        detail = exc.detail

        if isinstance(detail, dict):
            return {
                field: self._extract_messages(value) for field, value in detail.items()
            }

        if isinstance(detail, list):
            return {"non_field_errors": [str(err) for err in detail]}

        return {"error": [str(detail)]}

    def _extract_messages(self, value):
        if isinstance(value, (list, tuple)):
            result = []
            for err in value:
                if isinstance(err, dict):
                    result.append(
                        {k: self._extract_messages(v) for k, v in err.items()}
                    )
                elif isinstance(err, (list, tuple)):
                    result.append(self._extract_messages(err))
                else:
                    result.append(str(err))
            return result

        if isinstance(value, dict):
            nested = self.validation_error_type(
                type("ValidationError", (), {"detail": value})()
            )
            return [msg for messages in nested.values() for msg in messages]

        return [str(value)]


class ReturnResponseMixin(HandleExceptionMixin):
    """
    Mixin for returning response.
    """

    def list(self, request, message=None, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        payload = response.data
        if isinstance(payload, Mapping):
            return Response(status=response.status_code, **payload)
        return Response(data=payload, message=message, status=response.status_code)

        # if isinstance(data, dict) and 'count' in data and 'data' in data:
        #     pagination_kwargs = {k: v for k, v in data.items() if k != 'data'}
        #     data = data['data']
        #
        # return Response(
        #     data=data, message=message, status=response.status_code, **pagination_kwargs
        # )

    def create(self, request, message=None, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return Response(
            data=response.data, message=message, status=response.status_code
        )

    def retrieve(self, request, message=None, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        return Response(
            data=response.data, message=message, status=response.status_code
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return DRFResponse(status=204)  # don't return any content

    def update(self, request, message=None, *args, **kwargs):
        kwargs["partial"] = True
        response = super().update(request, *args, **kwargs)
        return Response(
            data=response.data, message=message, status=response.status_code
        )

    def partial_update(self, request, message=None, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)
