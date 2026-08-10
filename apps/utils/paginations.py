from collections import OrderedDict
from math import ceil
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from rest_framework.pagination import PageNumberPagination

from apps.utils.response import Response


class BasePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 1000

    def get_paginated_response(self, data, success=True, error=None, message=None):
        return Response(**self.get_paginated_data(data, success, error, message))

    def get_paginated_data(self, data, success=True, error=None, message=None):
        return OrderedDict(
            [
                ("count", self.page.paginator.count),
                ("current", self.page.number),
                ("total_pages", self.page.paginator.num_pages),
                ("per_page", self.page.paginator.per_page),
                ("next", self.get_next_link()),
                ("previous", self.get_previous_link()),
                ("data", data),
            ]
        )


def build_url(url, total_pages, page_num, page_size):
    if page_num < 1 or page_num > total_pages:
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query.update({"page": [page_num], "page_size": [page_size]})
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def paginator(data, request, count: int, page: int = 1, page_size: int = 20):
    url = request.build_absolute_uri()
    total_pages = ceil(count / page_size) if page_size else 1

    return OrderedDict(
        [
            ("count", count),
            ("current", page),
            ("total_pages", total_pages),
            ("per_page", page_size),
            ("next", build_url(url, total_pages, page + 1, page_size)),
            ("previous", build_url(url, total_pages, page - 1, page_size)),
            ("data", data),
        ]
    )
