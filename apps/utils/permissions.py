from django.urls.resolvers import get_resolver
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.utils.functions import get_perm_dict


def check_user_type(request, _type):
    """Checks if request user is specific type"""

    return bool(
        request.user
        and request.user.is_authenticated
        and getattr(request.user, _type, False)
    )


def check_user_role(request, role):
    """
    Check whether the authenticated user has the given role.
    eg: check_user_role(request, PositionChoices.SUPERADMIN)
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    credentials = getattr(request, "credentials", {})
    return credentials.get("position") == role


def amanat_staffs(request):
    """
    Determines whether the current user is an authenticated staff member of AmanatPay.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False
    credentials = getattr(request, "credentials", {})
    tin = credentials.get("company_tin")
    return tin == "309729912"


def amanat_staff_by_role(role):
    return bool(
        role
        and getattr(role, "company", None)
        and role.company.tin == "309729912"
    )


class DenyAll(BasePermission):
    def has_permission(self, request, view):
        return False


class ApiPermission(BasePermission):
    """
    Custom permission class to check user permissions.
    using view resolved name like "module1:module2:module3:page"

    """

    def has_permission(self, request, view):
        if not getattr(request.user, "is_authenticated", False):
            return False
        if not request.user.is_active:
            return False
        if getattr(request.user, "must_change_password", False):
            raise PermissionDenied(_("Parolni yangilashingiz shart"))

        credentials = getattr(request, "credentials", None)
        if not credentials:
            return False

        role_id = request.credentials.get("role_id")
        perm_dict = get_perm_dict(role_id)

        user_permissions = perm_dict.get("permissions", {})
        view_permission = get_resolver().resolve(request.path_info).view_name
        # print(view_permission)
        return user_permissions.get(view_permission, False)


class IsAmanatStaff(BasePermission):
    """
    Allows access only to amanat staffs.
    """

    def has_permission(self, request, view):
        return amanat_staffs(request)


class IsSuperAdminUser(BasePermission):
    """
    Allows access only to superadmin.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)
