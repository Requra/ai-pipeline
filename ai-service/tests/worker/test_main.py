from unittest.mock import MagicMock

import pytest

from app.worker.main import _connect_redis_with_retry


def test_worker_retries_redis_until_it_is_ready(monkeypatch):
    monkeypatch.setattr("app.worker.main._REDIS_CONNECT_ATTEMPTS", 3)
    monkeypatch.setattr("app.worker.main._REDIS_CONNECT_DELAY_SECONDS", 0)
    connection = MagicMock()
    connection.ping.side_effect = [ConnectionError("starting"), True]
    factory = MagicMock(return_value=connection)

    resolved = _connect_redis_with_retry(factory)

    assert resolved is connection
    assert factory.call_count == 2


def test_worker_raises_only_after_bounded_redis_retries(monkeypatch):
    monkeypatch.setattr("app.worker.main._REDIS_CONNECT_ATTEMPTS", 2)
    monkeypatch.setattr("app.worker.main._REDIS_CONNECT_DELAY_SECONDS", 0)
    factory = MagicMock(side_effect=ConnectionError("unavailable"))

    with pytest.raises(RuntimeError, match="Redis was not reachable"):
        _connect_redis_with_retry(factory)

    assert factory.call_count == 2
