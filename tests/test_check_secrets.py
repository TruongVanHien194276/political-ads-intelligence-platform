from scripts.check_secrets import scan_file


def test_scanner_catches_key_and_credential_filename(tmp_path) -> None:
    key = tmp_path / "service-account.json"
    key.write_text('{"type": "service_account", "private_key": "x"}')
    assert scan_file(key) == ["credential filename", "Google service account key"]

    env = tmp_path / ".env"
    env.write_text("PUBLIC_VALUE=1")
    assert scan_file(env) == ["credential filename"]


def test_scanner_allows_plain_source(tmp_path) -> None:
    source = tmp_path / "source.py"
    source.write_text("print('hello')")
    assert scan_file(source) == []
