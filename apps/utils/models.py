import uuid
from itertools import chain
from typing import Optional, Set

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import NotSupportedError, models
from django.db.models import Aggregate, F, FilteredRelation, Q
from django.db.models.constraints import UniqueConstraint
from django.utils import timezone
from django.utils.functional import classproperty
from django.utils.translation import gettext_lazy as _


DJANGO_FILTER_KEYS = [
    "contains",
    "day",
    "endswith",
    "exact",
    "gt",
    "gte",
    "hour",
    "icontains",
    "iexact",
    "iendswith",
    "in",
    "iregex",
    "isnull",
    "istartswith",
    "lt",
    "lte",
    "minute",
    "month",
    "range",
    "regex",
    "second",
    "startswith",
    "week_day",
    "year",
]


def is_relation(model, field_name):
    """Check if a field in a model (or a related model) is a relation field."""
    if not field_name:
        return False

    parts = field_name.split("__")
    current_model = model
    field = None  # Initialize field variable

    for part in parts:
        try:
            field = current_model._meta.get_field(part)
            # Check if the field is a relation (ForeignKey, OneToOneField, or ManyToManyField)
            if field.is_relation:
                current_model = field.related_model  # Move to the related model
            else:
                # If we reach a non-relation field, return False
                return False
        except FieldDoesNotExist:
            return False

    # If we traverse the entire chain, and the last field is a relation, return True
    return field is not None and field.is_relation


class FakeRemoveQuerySet(models.QuerySet):
    def delete(self):
        # Instead of actually deleting the objects, we perform a soft delete
        for obj in self:
            obj.delete()
        # return self.update(is_deleted=True, deleted_at=timezone.now())
        return self

    def restore(self):
        # Instead of actually deleting the objects, we perform a soft delete
        for obj in self:
            obj.restore()
        # return self.update(is_deleted=False, deleted_at=None)
        return self

    def _not_support_combined_queries(self, operation_name):
        if self.query.combinator:
            raise NotSupportedError(
                "Calling QuerySet.%s() after %s() is not supported."
                % (operation_name, self.query.combinator)
            )

    def annotate(self, *args, **kwargs):
        # print("Annotate: ", args, kwargs)
        self._not_support_combined_queries("annotate")
        return self._annotate(args, kwargs, select=True)

    def _annotate(self, args, kwargs, select=True):
        self._validate_values_are_expressions(
            args + tuple(kwargs.values()), method_name="annotate"
        )
        annotations = {}
        for arg in args:
            # The default_alias property may raise a TypeError.
            try:
                if arg.default_alias in kwargs:
                    raise ValueError(
                        "The named annotation '%s' conflicts with the "
                        "default name for another annotation." % arg.default_alias
                    )
            except TypeError:
                raise TypeError("Complex annotations require an alias")
            annotations[arg.default_alias] = arg
        annotations.update(kwargs)
        for alias, annotation in annotations.items():
            if isinstance(annotation, Aggregate):
                _filter = annotation.filter or Q()
                f = annotation.get_source_expressions()[0]
                if isinstance(f, F):
                    label = f.name
                    if is_relation(self.model, label):
                        relations = label.split("__")
                    else:
                        relations = label.split("__")[:-1]
                    related_filter = {}
                    for index, relation in enumerate(relations):
                        key = f"{'__'.join(relations[:index + 1])}__is_deleted"
                        related_filter[key] = False
                    annotation.filter = _filter & Q(**related_filter)
        clone = self._chain()
        names = self._fields
        if names is None:
            names = set(
                chain.from_iterable(
                    (
                        (field.name, field.attname)
                        if hasattr(field, "attname")
                        else (field.name,)
                    )
                    for field in self.model._meta.get_fields()
                )
            )

        for alias, annotation in annotations.items():
            if alias in names:
                raise ValueError(
                    "The annotation '%s' conflicts with a field on "
                    "the model." % alias
                )
            if isinstance(annotation, FilteredRelation):
                clone.query.add_filtered_relation(annotation, alias)
            else:
                clone.query.add_annotation(
                    annotation,
                    alias,
                    select=select,
                )
        for alias, annotation in clone.query.annotations.items():
            if alias in annotations and annotation.contains_aggregate:
                if clone._fields is None:
                    clone.query.group_by = True
                else:
                    clone.query.set_group_by()
                break
        return clone


class FakeRemoveManager(models.Manager):
    def get_queryset(self):
        return FakeRemoveQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def filter(self, *args, **kwargs):
        relations = set()
        for key in kwargs.keys():
            spitted_keys = key.split("__")
            if "__" in key:
                last_key = spitted_keys[-1]
                if last_key in DJANGO_FILTER_KEYS:
                    continue
                else:
                    relations.add("__".join(spitted_keys[:-1]))
        for relation in relations:
            related_filter = f"{relation}__is_deleted"
            kwargs[related_filter] = False
        return super().filter(*args, **kwargs)

    def all_with_deleted(self):
        """Returns all objects, including deleted ones."""
        return FakeRemoveQuerySet(self.model).all()

    def deleted_only(self):
        """Returns only deleted objects."""
        return FakeRemoveQuerySet(self.model).filter(is_deleted=True)

    def create(self, **kwargs):
        role_uuid = kwargs.pop("role_uuid", None)  # log yozish uchun kerak
        obj = self.model(**kwargs)
        self._for_write = True
        if role_uuid:  # log yozish uchun kerak
            setattr(obj, "role_uuid", role_uuid)
        obj.save(force_insert=True, using=self.db)
        return obj


class UuidModel(models.Model):
    """
    An abstract base class model that provides a UUID field.
    """

    uuid = models.UUIDField(
        _("UUID"), unique=True, editable=False, db_index=True, default=uuid.uuid4
    )

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """
    An abstract base class model that provides self-updating
    ``created_at`` and ``updated_at`` fields.

    """

    created_at = models.DateTimeField(
        default=timezone.now, verbose_name=_("Yaratilgan vaqt")
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Yangilangan vaqt"))

    def save(self, *args, **kwargs):
        """
        Overriding the save method in order to make sure that
        an updated_at field is updated even if it is not given as
        a parameter to the update field argument.
        """
        update_fields: Optional[Set[str]] = kwargs.get("update_fields")

        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"updated_at"}

        super().save(*args, **kwargs)

    class Meta:
        abstract = True


class FakeRemoveModel(models.Model):
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = FakeRemoveManager()
    with_deleted = models.Manager()

    class Meta:
        abstract = True
        # indexes = [
        #     models.Index(fields=["is_deleted"], name="idx_is_deleted_false", condition=models.Q(is_deleted=False)),
        # ]

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save()
        for related_object in self._meta.related_objects:
            if related_object.on_delete == models.CASCADE:
                related_model = related_object.related_model
                if hasattr(related_model, "objects"):
                    _filter = {related_object.field.name: self}
                    related_model.objects.filter(**_filter).delete()

    def restore(self, using=None, keep_parents=False):
        self.is_deleted = False
        self.deleted_at = None
        self.save()
        for related_object in self._meta.related_objects:
            if related_object.on_delete == models.CASCADE:
                related_model = related_object.related_model
                if hasattr(related_model, "objects") and hasattr(related_model.objects, "deleted_only"):
                    _filter = {related_object.field.name: self}
                    related_model.objects.deleted_only().filter(**_filter).restore()

    def hard_delete(self):
        super().delete()

    def _get_soft_delete_unique_constraints(self):
        """
        Meta.constraints dan soft delete UniqueConstraint larni olish
        """
        unique_fields = []
        unique_together = []

        constraints = getattr(self._meta, 'constraints', [])

        for constraint in constraints:
            if isinstance(constraint, UniqueConstraint):
                if constraint.condition is not None:
                    fields = list(constraint.fields)
                    if len(fields) == 1:
                        unique_fields.append(fields[0])
                    else:
                        unique_together.append(fields)

        return unique_fields, unique_together

    def clean(self):
        super().clean()
        errors = {}

        unique_fields, unique_together = self._get_soft_delete_unique_constraints()

        # Check single unique fields
        for field_name in unique_fields:
            field_value = getattr(self, field_name, None)
            if field_value is not None and field_value != '':
                qs = self.__class__.objects.filter(
                    **{field_name: field_value},
                    is_deleted=False
                )
                if self.pk:
                    qs = qs.exclude(pk=self.pk)

                if qs.exists():
                    field = self._meta.get_field(field_name)
                    verbose_name = getattr(field, 'verbose_name', field_name)
                    errors[field_name] = _(
                        "Bu %(field)s allaqachon ro'yxatdan o'tgan."
                    ) % {'field': verbose_name}

        # Check unique_together fields
        for field_names in unique_together:
            filter_kwargs = {'is_deleted': False}
            all_fields_have_values = True

            for field_name in field_names:
                field_value = getattr(self, field_name, None)
                if field_value is None:
                    all_fields_have_values = False
                    break
                filter_kwargs[field_name] = field_value

            if all_fields_have_values:
                qs = self.__class__.objects.filter(**filter_kwargs)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)

                if qs.exists():
                    first_field = field_names[0]
                    field_verbose_names = [
                        str(self._meta.get_field(f).verbose_name)
                        for f in field_names
                    ]
                    errors[first_field] = _(
                        "Bu kombinatsiya (%(fields)s) allaqachon mavjud."
                    ) % {'fields': ', '.join(field_verbose_names)}

        if errors:
            raise ValidationError(errors)


class BaseModel(UuidModel, TimeStampedModel, FakeRemoveModel):
    """
    An abstract base class model that provides self-updating
    fields:
    - uuid
    - created_at
    - updated_at
    - is_deleted
    - deleted_at
    """

    class Meta:
        abstract = True

    @classproperty
    def created_message(cls) -> str:
        verbose_name = cls._meta.verbose_name or cls.__name__
        return _("%(model)s yaratildi") % {"model": verbose_name}
