from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import sleep
from urllib.parse import quote_plus

import jwt
import pytest
from httpx import Client as HttpxClient
from httpx import ConnectError, ConnectTimeout, RemoteProtocolError, Request, Response

from meilisearch_python_sdk import Client
from meilisearch_python_sdk.errors import (
    InvalidRestriction,
    MeilisearchApiError,
    MeilisearchCommunicationError,
    MeilisearchTaskFailedError,
    MeilisearchTimeoutError,
)
from meilisearch_python_sdk.models.client import KeyCreate, KeyUpdate
from meilisearch_python_sdk.models.index import IndexInfo
from meilisearch_python_sdk.models.version import Version
from meilisearch_python_sdk.types import JsonDict


@pytest.fixture
def remove_default_search_key(default_search_key, test_client):
    test_client.delete_key(default_search_key.key)
    yield
    key = KeyCreate(
        description=default_search_key.description,
        actions=default_search_key.actions,
        indexes=default_search_key.indexes,
        expires_at=default_search_key.expires_at,
    )
    test_client.create_key(key)


@pytest.fixture
def test_key(test_client):
    key_info = KeyCreate(description="test", actions=["search"], indexes=["movies"])
    key = test_client.create_key(key_info)

    yield key

    try:
        test_client.delete_key(key.key)
    except MeilisearchApiError:
        pass


@pytest.fixture
def test_key_info(test_client):
    key_info = KeyCreate(description="test", actions=["search"], indexes=["movies"])

    yield key_info

    try:
        keys = test_client.get_keys()
        key = next(x for x in keys.results if x.description == key_info.description)
        test_client.delete_key(key.key)
    except MeilisearchApiError:
        pass


def wait_for_dump_creation(test_client, dump_uid, timeout_in_ms=10000.0, interval_in_ms=500.0):
    start_time = datetime.now()
    elapsed_time = 0.0
    while elapsed_time < timeout_in_ms:
        dump = test_client.get_dump_status(dump_uid)
        if dump.status != "in_progress":
            return None
        sleep(interval_in_ms / 1000)
        time_delta = datetime.now() - start_time
        elapsed_time = time_delta.seconds * 1000 + time_delta.microseconds / 1000
    raise TimeoutError


def test_create_index_with_primary_key(test_client):
    uid = "test"
    primary_key = "pk_test"
    index = test_client.create_index(uid=uid, primary_key=primary_key)

    assert index.uid == uid

    assert index.primary_key == primary_key
    assert isinstance(index.created_at, datetime)
    assert isinstance(index.updated_at, datetime)


def test_create_index_no_primary_key(test_client):
    uid = "test"
    index = test_client.create_index(uid=uid)

    assert index.uid == uid

    assert index.primary_key is None
    assert isinstance(index.created_at, datetime)
    assert isinstance(index.updated_at, datetime)


def test_create_keys_with_wildcarded_actions(test_client, test_key_info):
    test_key_info.actions = ["documents.*"]
    key = test_client.create_key(test_key_info)

    assert key.actions == ["documents.*"]


def test_generate_tenant_token_custom_key(test_client, test_key):
    search_rules = {"test": "value"}
    expected = {"searchRules": search_rules, "apiKeyUid": test_key.uid}
    token = test_client.generate_tenant_token(search_rules, api_key=test_key)
    assert expected == jwt.decode(jwt=token, key=test_key.key, algorithms=["HS256"])


def test_generate_tenant_token_default_key(test_client, default_search_key):
    search_rules = {"test": "value"}
    expected = {"searchRules": search_rules, "apiKeyUid": default_search_key.uid}
    token = test_client.generate_tenant_token(search_rules, api_key=default_search_key)
    assert expected == jwt.decode(jwt=token, key=default_search_key.key, algorithms=["HS256"])


def test_generate_tenant_token_default_key_expires(test_client, default_search_key):
    search_rules: JsonDict = {"test": "value"}
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=1)
    expected: JsonDict = {"searchRules": search_rules}
    expected["apiKeyUid"] = default_search_key.uid
    expected["exp"] = int(datetime.timestamp(expires_at))
    token = test_client.generate_tenant_token(
        search_rules, api_key=default_search_key, expires_at=expires_at
    )
    assert expected == jwt.decode(jwt=token, key=default_search_key.key, algorithms=["HS256"])


def test_generate_tenant_token_default_key_expires_past(test_client, default_search_key):
    search_rules: JsonDict = {"test": "value"}
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=-1)
    with pytest.raises(ValueError):
        test_client.generate_tenant_token(
            search_rules, api_key=default_search_key, expires_at=expires_at
        )


def test_generate_tenant_token_invalid_restriction(test_key_info, test_client):
    test_key_info.indexes = ["good"]
    key = test_client.create_key(test_key_info)
    payload = {"indexes": ["bad"]}

    with pytest.raises(InvalidRestriction):
        test_client.generate_tenant_token(payload, api_key=key)


@pytest.mark.usefixtures("indexes_sample")
def test_get_indexes(test_client, index_uid, index_uid2):
    response = test_client.get_indexes()
    response_uids = [x.uid for x in response]

    assert index_uid in response_uids
    assert index_uid2 in response_uids
    assert len(response) == 2


@pytest.mark.usefixtures("indexes_sample")
def test_get_indexes_offset_and_limit(test_client):
    response = test_client.get_indexes(offset=1, limit=1)
    assert len(response) == 1


@pytest.mark.usefixtures("indexes_sample")
def test_get_indexes_offset(test_client):
    response = test_client.get_indexes(offset=1)
    assert len(response) >= 1 and len(response) <= 20


@pytest.mark.usefixtures("indexes_sample")
def test_get_indexes_limit(test_client):
    response = test_client.get_indexes(limit=1)
    assert len(response) == 1


def test_get_indexes_none(test_client):
    response = test_client.get_indexes()

    assert response is None


@pytest.mark.usefixtures("indexes_sample")
def test_get_index(test_client, index_uid):
    response = test_client.get_index(index_uid)

    assert response.uid == index_uid
    assert response.primary_key is None
    assert isinstance(response.created_at, datetime)
    assert isinstance(response.updated_at, datetime)


def test_get_index_not_found(test_client):
    with pytest.raises(MeilisearchApiError):
        test_client.get_index("test")


def test_index(test_client):
    uid = "test"
    response = test_client.index(uid)

    assert response.uid == uid


def test_get_or_create_index_with_primary_key(test_client):
    primary_key = "pk_test"
    uid = "test1"
    response = test_client.get_or_create_index(uid, primary_key)

    assert response.uid == uid
    assert response.primary_key == primary_key


def test_get_or_create_index_no_primary_key(test_client):
    uid = "test"
    response = test_client.get_or_create_index(uid)

    assert response.uid == uid
    assert response.primary_key is None


def test_get_or_create_index_communication_error(test_client, monkeypatch):
    def mock_get_response(*args, **kwargs):
        raise ConnectError("test", request=Request("GET", url="http://localhost"))

    def mock_post_response(*args, **kwargs):
        raise ConnectError("test", request=Request("POST", url="http://localhost"))

    monkeypatch.setattr(HttpxClient, "get", mock_get_response)
    monkeypatch.setattr(HttpxClient, "post", mock_post_response)
    with pytest.raises(MeilisearchCommunicationError):
        test_client.get_or_create_index("test")


def test_get_or_create_index_api_error(test_client, monkeypatch):
    def mock_response(*args, **kwargs):
        raise MeilisearchApiError("test", Response(status_code=404))

    monkeypatch.setattr(Client, "get_index", mock_response)
    with pytest.raises(MeilisearchApiError):
        test_client.get_or_create_index("test")


@pytest.mark.usefixtures("indexes_sample")
def test_get_all_stats(test_client, index_uid, index_uid2):
    response = test_client.get_all_stats()

    assert index_uid in response.indexes
    assert index_uid2 in response.indexes


@pytest.mark.usefixtures("indexes_sample")
def test_get_raw_index(test_client, index_uid):
    response = test_client.get_raw_index(index_uid)

    assert response.uid == index_uid
    assert isinstance(response, IndexInfo)


def test_get_raw_index_none(test_client):
    response = test_client.get_raw_index("test")

    assert response is None


@pytest.mark.usefixtures("indexes_sample")
def test_get_raw_indexes(test_client, index_uid, index_uid2):
    response = test_client.get_raw_indexes()
    response_uids = [x.uid for x in response]

    assert index_uid in response_uids
    assert index_uid2 in response_uids
    assert len(response) == 2


@pytest.mark.usefixtures("indexes_sample")
def test_get_raw_indexes_offset_and_limit(test_client):
    response = test_client.get_raw_indexes(offset=1, limit=1)
    assert len(response) == 1


@pytest.mark.usefixtures("indexes_sample")
def test_get_raw_indexes_offset(test_client):
    response = test_client.get_raw_indexes(offset=1)
    assert len(response) >= 1 and len(response) <= 20


@pytest.mark.usefixtures("indexes_sample")
def test_get_raw_indexes_limit(test_client):
    response = test_client.get_raw_indexes(limit=1)
    assert len(response) == 1


def test_get_raw_indexes_none(test_client):
    response = test_client.get_raw_indexes()

    assert response is None


def test_health(test_client):
    health = test_client.health()

    assert health.status == "available"


def test_create_key(test_key_info, test_client):
    expires_at = datetime.now(tz=timezone.utc) + timedelta(days=2)
    test_key_info.expires_at = expires_at
    key = test_client.create_key(test_key_info)

    assert key.description == test_key_info.description
    assert key.actions == test_key_info.actions
    assert key.indexes == test_key_info.indexes
    assert key.expires_at == expires_at.replace(tzinfo=None)


def test_create_key_no_expires(test_key_info, test_client):
    key = test_client.create_key(test_key_info)

    assert key.description == test_key_info.description
    assert key.actions == test_key_info.actions
    assert key.indexes == test_key_info.indexes
    assert key.expires_at is None


def test_delete_key(test_key, test_client):
    result = test_client.delete_key(test_key.key)
    assert result == 204

    with pytest.raises(MeilisearchApiError):
        test_client.get_key(test_key.key)


def test_get_keys(test_client):
    response = test_client.get_keys()
    assert len(response.results) == 2


def test_get_keys_offset_and_limit(test_client):
    response = test_client.get_keys(offset=1, limit=1)
    assert len(response.results) == 1


def test_get_keys_offset(test_client):
    response = test_client.get_keys(offset=1)
    assert len(response.results) >= 1 and len(response.results) <= 20


def test_get_keys_limit(test_client):
    response = test_client.get_keys(limit=1)
    assert len(response.results) == 1


def test_get_key(test_key, test_client):
    key = test_client.get_key(test_key.key)
    assert key.description == test_key.description


def test_update_key(test_key, test_client):
    update_key_info = KeyUpdate(
        key=test_key.key,
        description="updated",
    )

    key = test_client.update_key(update_key_info)

    assert key.description == update_key_info.description
    assert key.actions == test_key.actions
    assert key.indexes == test_key.indexes
    assert key.expires_at == test_key.expires_at


def test_get_version(test_client):
    response = test_client.get_version()

    assert isinstance(response, Version)


def test_create_dump(test_client, index_with_documents):
    index_with_documents()
    response = test_client.create_dump()
    test_client.wait_for_task(response.task_uid)

    dump_status = test_client.get_task(response.task_uid)
    assert dump_status.status == "succeeded"
    assert dump_status.task_type == "dumpCreation"


def test_no_master_key(base_url):
    with pytest.raises(MeilisearchApiError):
        client = Client(base_url)
        client.create_index("some_index")


def test_bad_master_key(base_url, master_key):
    with pytest.raises(MeilisearchApiError):
        client = Client(base_url)
        client.create_index("some_index", f"{master_key}bad")


def test_communication_error(master_key):
    with pytest.raises(MeilisearchCommunicationError):
        client = Client("http://wrongurl:1234", master_key, timeout=1)
        client.create_index("some_index")


def test_remote_protocol_error(test_client, monkeypatch):
    def mock_error(*args, **kwargs):
        raise RemoteProtocolError("error", request=args[0])

    monkeypatch.setattr(HttpxClient, "post", mock_error)
    with pytest.raises(MeilisearchCommunicationError):
        test_client.create_index("some_index")


def test_connection_timeout(test_client, monkeypatch):
    def mock_error(*args, **kwargs):
        raise ConnectTimeout("error")

    monkeypatch.setattr(HttpxClient, "post", mock_error)
    with pytest.raises(MeilisearchCommunicationError):
        test_client.create_index("some_index")


def test_swap_indexes(test_client, empty_index):
    index_a = empty_index()
    index_b = empty_index()
    task_a = index_a.add_documents([{"id": 1, "title": index_a.uid}])
    task_b = index_b.add_documents([{"id": 1, "title": index_b.uid}])
    test_client.wait_for_task(task_a.task_uid)
    test_client.wait_for_task(task_b.task_uid)
    swapTask = test_client.swap_indexes([(index_a.uid, index_b.uid)])
    task = test_client.wait_for_task(swapTask.task_uid)
    doc_a = test_client.index(index_a.uid).get_document(1)
    doc_b = test_client.index(index_b.uid).get_document(1)

    assert doc_a["title"] == index_b.uid
    assert doc_b["title"] == index_a.uid
    assert task.task_type == "indexSwap"


@pytest.mark.usefixtures("create_tasks")
def test_cancel_statuses(test_client):
    task = test_client.cancel_tasks(statuses=["enqueued", "processing"])
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.index_uid is None
    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert "statuses=enqueued%2Cprocessing" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_uids(test_client):
    task = test_client.cancel_tasks(uids=["1", "2"])
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert "uids=1%2C2" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_index_uids(test_client):
    task = test_client.cancel_tasks(index_uids=["1"])

    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert "indexUids=1" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_types(test_client):
    task = test_client.cancel_tasks(types=["taskDeletion"])
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert "types=taskDeletion" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_before_enqueued_at(test_client):
    before = datetime.now()
    task = test_client.cancel_tasks(before_enqueued_at=before)
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert (
        f"beforeEnqueuedAt={quote_plus(before.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_after_enqueued_at(test_client):
    after = datetime.now()
    task = test_client.cancel_tasks(after_enqueued_at=after)
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert (
        f"afterEnqueuedAt={quote_plus(after.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_before_started_at(test_client):
    before = datetime.now()
    task = test_client.cancel_tasks(before_started_at=before)
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert (
        f"beforeStartedAt={quote_plus(before.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_cancel_tasks_after_finished_at(test_client):
    after = datetime.now()
    task = test_client.cancel_tasks(after_finished_at=after)
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert (
        f"afterFinishedAt={quote_plus(after.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_cancel_task_no_params(test_client):
    task = test_client.cancel_tasks()
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskCancelation")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskCancelation"
    assert tasks.results[0].details is not None
    assert "statuses=enqueued%2Cprocessing" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_delete_statuses(test_client):
    task = test_client.delete_tasks(statuses=["enqueued", "processing"])
    test_client.wait_for_task(task.task_uid)
    deleted_tasks = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_tasks.status == "succeeded"
    assert deleted_tasks.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert "statuses=enqueued%2Cprocessing" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks(test_client):
    task = test_client.delete_tasks(uids=["1", "2"])
    test_client.wait_for_task(task.task_uid)
    completed_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert completed_task.status == "succeeded"
    assert completed_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert "uids=1%2C2" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks_index_uids(test_client):
    task = test_client.delete_tasks(index_uids=["1"])
    test_client.wait_for_task(task.task_uid)
    deleted_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_task.status == "succeeded"
    assert deleted_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert "indexUids=1" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks_types(test_client):
    task = test_client.delete_tasks(types=["taskDeletion"])
    test_client.wait_for_task(task.task_uid)
    deleted_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_task.status == "succeeded"
    assert deleted_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert "types=taskDeletion" in tasks.results[0].details["originalFilter"]


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks_before_enqueued_at(test_client):
    before = datetime.now()
    task = test_client.delete_tasks(before_enqueued_at=before)
    test_client.wait_for_task(task.task_uid)
    deleted_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_task.status == "succeeded"
    assert deleted_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert (
        f"beforeEnqueuedAt={quote_plus(before.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks_after_enqueued_at(test_client):
    after = datetime.now()
    task = test_client.delete_tasks(after_enqueued_at=after)
    test_client.wait_for_task(task.task_uid)
    deleted_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_task.status == "succeeded"
    assert deleted_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert (
        f"afterEnqueuedAt={quote_plus(after.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks_before_started_at(test_client):
    before = datetime.now()
    task = test_client.delete_tasks(before_started_at=before)
    test_client.wait_for_task(task.task_uid)
    deleted_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_task.status == "succeeded"
    assert deleted_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert (
        f"beforeStartedAt={quote_plus(before.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_delete_tasks_after_finished_at(test_client):
    after = datetime.now()
    task = test_client.delete_tasks(after_finished_at=after)
    test_client.wait_for_task(task.task_uid)
    deleted_task = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_task.status == "succeeded"
    assert deleted_task.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert (
        f"afterFinishedAt={quote_plus(after.isoformat())}Z"
        in tasks.results[0].details["originalFilter"]
    )


@pytest.mark.usefixtures("create_tasks")
def test_delete_no_params(test_client):
    task = test_client.delete_tasks()
    test_client.wait_for_task(task.task_uid)
    deleted_tasks = test_client.get_task(task.task_uid)
    tasks = test_client.get_tasks(types="taskDeletion")

    assert deleted_tasks.status == "succeeded"
    assert deleted_tasks.task_type == "taskDeletion"
    assert tasks.results[0].details is not None
    assert (
        "statuses=canceled%2Cenqueued%2Cfailed%2Cprocessing%2Csucceeded"
        in tasks.results[0].details["originalFilter"]
    )


def test_get_tasks(test_client, empty_index, small_movies):
    index = empty_index()
    tasks = test_client.get_tasks()
    current_tasks = len(tasks.results)
    response = index.add_documents(small_movies)
    test_client.wait_for_task(response.task_uid)
    response = index.add_documents(small_movies)
    test_client.wait_for_task(response.task_uid)
    response = test_client.get_tasks()
    assert len(response.results) >= current_tasks


def test_get_tasks_for_index(test_client, empty_index, small_movies):
    index = empty_index()
    tasks = test_client.get_tasks(index_ids=[index.uid])
    current_tasks = len(tasks.results)
    response = index.add_documents(small_movies)
    test_client.wait_for_task(response.task_uid)
    response = index.add_documents(small_movies)
    test_client.wait_for_task(response.task_uid)
    response = test_client.get_tasks(index_ids=[index.uid])
    assert len(response.results) >= current_tasks
    uid = set([x.index_uid for x in response.results])
    assert len(uid) == 1
    assert next(iter(uid)) == index.uid


def test_get_task(test_client, empty_index, small_movies):
    index = empty_index()
    response = index.add_documents(small_movies)
    test_client.wait_for_task(response.task_uid)
    update = test_client.get_task(response.task_uid)
    assert update.status == "succeeded"


def test_wait_for_task(test_client, empty_index, small_movies):
    index = empty_index()
    response = index.add_documents(small_movies)
    update = test_client.wait_for_task(response.task_uid)
    assert update.status == "succeeded"


def test_wait_for_task_no_timeout(test_client, empty_index, small_movies):
    index = empty_index()
    response = index.add_documents(small_movies)
    update = test_client.wait_for_task(response.task_uid, timeout_in_ms=None)
    assert update.status == "succeeded"


def test_wait_for_pending_update_time_out(test_client, empty_index, small_movies):
    index = empty_index()
    with pytest.raises(MeilisearchTimeoutError):
        response = index.add_documents(small_movies)
        test_client.wait_for_task(response.task_uid, timeout_in_ms=1, interval_in_ms=1)

    test_client.wait_for_task(  # Make sure the indexing finishes so subsequent tests don't have issues.
        response.task_uid
    )


def test_wait_for_task_raise_for_status_true(test_client, empty_index, small_movies):
    index = empty_index()
    response = index.add_documents(small_movies)
    update = test_client.wait_for_task(response.task_uid, raise_for_status=True)
    assert update.status == "succeeded"


def test_wait_for_task_raise_for_status_true_no_timeout(
    test_client, empty_index, small_movies, base_url, monkeypatch
):
    def mock_get_response(*args, **kwargs):
        task = {
            "uid": args[1].split("/")[1],
            "index_uid": "7defe207-8165-4b69-8170-471456e295e0",
            "status": "failed",
            "task_type": "indexDeletion",
            "details": {"deletedDocuments": 30},
            "error": None,
            "canceled_by": None,
            "duration": "PT0.002765250S",
            "enqueued_at": "2023-06-09T01:03:48.311936656Z",
            "started_at": "2023-06-09T01:03:48.314143377Z",
            "finished_at": "2023-06-09T01:03:48.316536088Z",
        }

        return Response(200, json=task, request=Request("get", url=f"{base_url}/{args[1]}"))

    index = empty_index()
    response = index.add_documents(small_movies)
    monkeypatch.setattr(HttpxClient, "get", mock_get_response)
    with pytest.raises(MeilisearchTaskFailedError):
        test_client.wait_for_task(response.task_uid, raise_for_status=True, timeout_in_ms=None)


def test_wait_for_task_raise_for_status_false(
    test_client, empty_index, small_movies, base_url, monkeypatch
):
    def mock_get_response(*args, **kwargs):
        task = {
            "uid": args[1].split("/")[1],
            "index_uid": "7defe207-8165-4b69-8170-471456e295e0",
            "status": "failed",
            "task_type": "indexDeletion",
            "details": {"deletedDocuments": 30},
            "error": None,
            "canceled_by": None,
            "duration": "PT0.002765250S",
            "enqueued_at": "2023-06-09T01:03:48.311936656Z",
            "started_at": "2023-06-09T01:03:48.314143377Z",
            "finished_at": "2023-06-09T01:03:48.316536088Z",
        }
        return Response(200, json=task, request=Request("get", url=f"{base_url}/{args[1]}"))

    index = empty_index()
    response = index.add_documents(small_movies)
    monkeypatch.setattr(HttpxClient, "get", mock_get_response)
    with pytest.raises(MeilisearchTaskFailedError):
        test_client.wait_for_task(response.task_uid, raise_for_status=True)
