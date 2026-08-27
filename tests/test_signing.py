import time

import pytest

from k4echo import signing

SECRET = "a" * 48
BODY = b'{"command":"power_off"}'


def test_round_trip():
    headers = signing.sign_request(SECRET, BODY)
    timestamp, nonce = signing.verify_request(SECRET, headers, BODY)
    assert nonce == headers[signing.HEADER_NONCE]
    assert timestamp == int(headers[signing.HEADER_TIMESTAMP])


def test_wrong_secret_is_rejected():
    headers = signing.sign_request(SECRET, BODY)
    with pytest.raises(signing.SignatureError, match="mismatch"):
        signing.verify_request("b" * 48, headers, BODY)


def test_tampered_body_is_rejected():
    headers = signing.sign_request(SECRET, BODY)
    with pytest.raises(signing.SignatureError, match="mismatch"):
        signing.verify_request(SECRET, headers, b'{"command":"power_on"}')


def test_stale_request_is_rejected():
    headers = signing.sign_request(SECRET, BODY, timestamp=int(time.time()) - 4000)
    with pytest.raises(signing.SignatureError, match="window"):
        signing.verify_request(SECRET, headers, BODY)


def test_future_request_is_rejected():
    headers = signing.sign_request(SECRET, BODY, timestamp=int(time.time()) + 4000)
    with pytest.raises(signing.SignatureError, match="window"):
        signing.verify_request(SECRET, headers, BODY)


@pytest.mark.parametrize("drop", [signing.HEADER_SIGNATURE, signing.HEADER_TIMESTAMP, signing.HEADER_NONCE])
def test_missing_headers_are_rejected(drop):
    headers = signing.sign_request(SECRET, BODY)
    del headers[drop]
    with pytest.raises(signing.SignatureError, match="missing"):
        signing.verify_request(SECRET, headers, BODY)


def test_headers_are_matched_case_insensitively():
    headers = {k.lower(): v for k, v in signing.sign_request(SECRET, BODY).items()}
    signing.verify_request(SECRET, headers, BODY)


def test_malformed_timestamp_is_rejected():
    headers = signing.sign_request(SECRET, BODY)
    headers[signing.HEADER_TIMESTAMP] = "not-a-number"
    with pytest.raises(signing.SignatureError, match="malformed"):
        signing.verify_request(SECRET, headers, BODY)


def test_replay_guard_rejects_a_reused_nonce():
    guard = signing.ReplayGuard()
    guard.check_and_record("abc")
    with pytest.raises(signing.SignatureError, match="already used"):
        guard.check_and_record("abc")


def test_replay_guard_forgets_old_nonces():
    guard = signing.ReplayGuard(window_seconds=100)
    now = time.time()
    guard.check_and_record("abc", now=now)
    guard.check_and_record("abc", now=now + 500)
