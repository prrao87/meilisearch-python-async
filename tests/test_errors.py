from httpx import Response

from meilisearch_python_async.errors import (
    MeilisearchApiError,
    MeilisearchCommunicationError,
    MeilisearchError,
    MeilisearchTimeoutError,
)


def test_meilisearch_api_error():
    expected = "test"
    got = MeilisearchApiError(expected, Response(status_code=404))

    assert expected in str(got)


def test_meilisearch_communication_error():
    expected = "test"
    got = MeilisearchCommunicationError(expected)

    assert expected in str(got)


def test_meilisearch_error():
    expected = "test message"
    got = MeilisearchError(expected)

    assert expected in str(got)


def test_meilisearch_timeout_error():
    expected = "test"
    got = MeilisearchTimeoutError(expected)

    assert expected in str(got)
