from rest_framework.relations import MANY_RELATION_KWARGS, ManyRelatedField, PrimaryKeyRelatedField


class CommaSeparatedManyRelatedField(ManyRelatedField):
    """Lets multipart/form-data send one field as "1,2,3" instead of repeating the key."""

    def get_value(self, dictionary):
        value = super().get_value(dictionary)
        if not isinstance(value, list):
            return value

        result = []
        for item in value:
            if isinstance(item, str) and "," in item:
                result.extend(part.strip() for part in item.split(",") if part.strip())
            else:
                result.append(item)
        return result


class CommaSeparatedPKField(PrimaryKeyRelatedField):
    @classmethod
    def many_init(cls, *args, **kwargs):
        list_kwargs = {"child_relation": cls(*args, **kwargs)}
        for key in kwargs:
            if key in MANY_RELATION_KWARGS:
                list_kwargs[key] = kwargs[key]
        return CommaSeparatedManyRelatedField(**list_kwargs)
