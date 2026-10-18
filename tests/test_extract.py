import pytest

from political_ads.config import load_settings
from political_ads.extract import BigQueryBackend, CostLimitError, run_extract


class FakeBackend:
    def __init__(self, estimated_bytes: int = 100) -> None:
        self.estimated_bytes = estimated_bytes
        self.fetch_called = False

    def estimate_bytes(self, sql: str, location: str) -> int:
        assert sql.startswith("SELECT")
        assert location == "US"
        return self.estimated_bytes

    def fetch_rows(
        self,
        sql: str,
        location: str,
        max_bytes: int,
        max_rows: int,
    ) -> list[dict[str, object]]:
        self.fetch_called = True
        assert max_bytes == 256 * 1024 * 1024
        assert max_rows == 500
        return [{"advertiser_id": "test-1", "spend_usd": 100}]


@pytest.fixture
def settings():
    return load_settings({"BIGQUERY_LOCATION": "US"})


def test_default_is_dry_run(settings) -> None:
    backend = FakeBackend()

    result = run_extract("SELECT 1", settings, backend)

    assert result.executed is False
    assert result.estimated_bytes == 100
    assert result.rows == []
    assert backend.fetch_called is False


def test_execute_uses_mock_backend(settings) -> None:
    backend = FakeBackend()

    result = run_extract("SELECT 1", settings, backend, execute=True)

    assert result.executed is True
    assert result.rows == [{"advertiser_id": "test-1", "spend_usd": 100}]
    assert backend.fetch_called is True


def test_cost_limit_blocks_fetch(settings) -> None:
    backend = FakeBackend(settings.max_bytes_billed + 1)

    with pytest.raises(CostLimitError):
        run_extract("SELECT 1", settings, backend, execute=True)

    assert backend.fetch_called is False


def test_rejects_multiple_statements(settings) -> None:
    backend = FakeBackend()

    with pytest.raises(ValueError):
        run_extract("SELECT 1; DELETE FROM x", settings, backend)

    assert backend.fetch_called is False


def test_invalid_config() -> None:
    with pytest.raises(ValueError):
        load_settings({"MAX_QUERY_MIB": "9999"})


def test_bigquery_adapter_maps_rows_and_keeps_billing_limit(monkeypatch) -> None:
    class FakeRow:
        def items(self):
            return [("advertiser_id", "test-1"), ("spend_usd", 100)]

    class FakeJob:
        total_bytes_processed = 100

        def result(self, max_results: int):
            assert max_results == 5
            return [FakeRow()]

    class FakeClient:
        def __init__(self):
            self.calls = []

        def query(self, sql, *, location, job_config):
            self.calls.append((sql, location, job_config))
            return FakeJob()

    client = FakeClient()
    monkeypatch.setattr("political_ads.extract.bigquery.Client", lambda project: client)
    backend = BigQueryBackend(None)

    assert backend.estimate_bytes("SELECT 1", "US") == 100
    assert backend.fetch_rows("SELECT 1", "US", 256 * 1024 * 1024, 5) == [
        {"advertiser_id": "test-1", "spend_usd": 100}
    ]
    assert client.calls[0][2].dry_run is True
    assert client.calls[1][2].maximum_bytes_billed == 256 * 1024 * 1024
