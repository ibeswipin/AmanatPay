from django.contrib import admin, messages
from django.core.exceptions import FieldDoesNotExist
from django.db.models import ForeignKey
from django.utils.translation import gettext_lazy as _

from apps.utils.functions import validate_kwargs_string
from apps.utils.models import FakeRemoveQuerySet


class AdminModelWithDeleted(admin.ModelAdmin):
    actions = ["restore_selected", "hard_delete_selected"]

    def restore_selected(self, request, queryset):
        """Action to restore multiple soft-deleted objects"""
        restored_count = queryset.filter(is_deleted=True).restore()
        self.message_user(request, f"{restored_count} object(s) restored successfully.")

    restore_selected.short_description = "Restore selected objects"

    def hard_delete_selected(self, request, queryset):
        """Action to hard delete multiple objects"""
        for item in queryset:
            item.hard_delete()

        model_name = queryset.model._meta.verbose_name_plural
        self.message_user(
            request, f"{len(queryset)} {model_name} hard deleted successfully."
        )

    hard_delete_selected.short_description = "Hard delete selected objects"

    def get_readonly_fields(self, request, obj=None):
        # Get the readonly fields from the parent class
        readonly_fields = list(super().get_readonly_fields(request, obj))

        if hasattr(self.model, "uuid"):
            readonly_fields.append("uuid")
        if hasattr(self.model, "deleted_at"):
            readonly_fields.append("deleted_at")
        if hasattr(self.model, "is_deleted"):
            readonly_fields.append("is_deleted")

        return readonly_fields

    def get_queryset(self, request):
        if hasattr(self.model.objects, "all_with_deleted"):
            return FakeRemoveQuerySet(self.model).all()
            # return self.model.objects.all_with_deleted()
        else:
            # Fallback to default queryset if 'all_with_deleted' does not exist
            return super().get_queryset(request)

    def get_list_display(self, request):
        # Get the list_display from the subclass and append "is_deleted"
        list_display = super().get_list_display(request)
        if hasattr(self.model, "is_deleted"):
            return list(list_display) + ["is_deleted"]
        return list_display

    def delete_queryset(self, request, queryset):
        try:
            # Get the 'deleted' field of the model
            self.model._meta.get_field("is_deleted")
            queryset.delete()

        except FieldDoesNotExist:
            # Fallback to default delete_queryset if 'is_deleted' field does not exist
            if request.user.is_superuser:
                return super().delete_queryset(request, queryset)


class DynamicFieldFilter(admin.SimpleListFilter):
    title = _("Field")
    parameter_name = "dynamic_field_filter"

    def lookups(self, request, model_admin):
        # Get the field names of the model dynamically
        field_names = [field.name for field in model_admin.model._meta.get_fields()]

        # Convert field names to (field_name, field_name) tuples for filter options
        return [(field_name, field_name) for field_name in field_names]

    def queryset(self, request, queryset):
        return queryset


class AdminSearchMixin(AdminModelWithDeleted):
    search_fields = ["pk"]
    """
    Admin search mixin
    """
    list_editable = ()

    def get_queryset(self, request):
        # Get the default queryset
        qs = super().get_queryset(request)

        # Get fields from list_display
        list_display_fields = getattr(self, "list_display")
        if list_display_fields == ("__str__",):
            list_display_fields = []
        # Get ForeignKey fields from the model
        fk_fields = [
            field.name
            for field in self.model._meta.get_fields()
            if isinstance(field, ForeignKey) and field.name in list_display_fields
        ]

        inner_selects = getattr(self, "inner_select_related", None)
        if inner_selects:
            qs = qs.select_related(*inner_selects)
        # Apply select_related for ForeignKey fields
        if fk_fields:
            qs = qs.select_related(*fk_fields)
        return qs

    def get_search_results(self, request, queryset, search_term):
        """
        Get search results method for AdminSearchMixin class returns queryset with search results and distinct flag.
        If '=' in search_term it tries to validate search_term with validate_kwargs_string function.
        If it's successfully validated it returns queryset which filtered by kwargs and distinct flag.
        If search_term doesn't contain '=' it returns queryset with search results and distinct flag
        Example search_term:
            "key1=value1,key2=value2" -> {"key1": "value1", "key2": "value2"}
            "key1=value1, key2=value2" -> {"key1": "value1", "key2": "value2"}
            "find" -> "find"
            "company1__name=Shoshmaqom klasteri, document__is_signed=True" ->
                -> {"company1__name": "Shoshmaqom klasteri", "document__is_signed": True}
        """
        queryset, distinct = super().get_search_results(request, queryset, search_term)
        if "=" in search_term:
            try:
                validated_kwargs = validate_kwargs_string(search_term)
                if validated_kwargs:
                    key = "paginate_by"
                    if key in validated_kwargs.keys():
                        paginate_by = validated_kwargs.pop(key)
                        self.list_per_page = int(paginate_by)
                    qs = self.model.objects.all_with_deleted().filter(
                        **validated_kwargs
                    )
                    return queryset | qs, distinct
            except Exception as e:
                messages.add_message(request, messages.WARNING, f"Search error: {e}")
        return queryset, distinct

    def get_list_display(self, request):
        try:
            # Get the selected field from the query parameters
            selected_field = request.GET.get("dynamic_field_filter")
            selected_editable_field = request.GET.get("dynamic_field_editable")

            # Specify default fields
            list_display = super().get_list_display(request)

            if selected_editable_field:
                self.list_editable = tuple(self.list_editable) + (
                    selected_editable_field,
                )
                list_display = tuple(list_display) + (selected_editable_field,)
            # If a field is selected, include it in the list display
            if selected_field:
                list_display = tuple(list_display) + (selected_field,)
            return list_display
        except Exception as e:
            messages.add_message(request, messages.WARNING, f"List display error: {e}")
            return super().get_list_display(request)

    def get_list_filter(self, request):
        try:
            return tuple(super().get_list_filter(request)) + (DynamicFieldFilter,)
        except Exception as e:
            messages.add_message(request, messages.WARNING, f"List filter error: {e}")
            return super().get_list_filter(request)
