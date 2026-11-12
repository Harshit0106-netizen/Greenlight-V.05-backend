import socket

from app.services import integration_security as sec


def fake_resolver(ip):
    return lambda host, port, proto=0: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


def test_api_keys_are_random_prefixed_and_hashed():
    a, prefix, h = sec.generate_api_key()
    b, _, _ = sec.generate_api_key()
    assert a != b and a.startswith("glk_") and a.startswith(prefix) and len(a) > 40
    assert h == sec.hash_key(a) and a not in h and len(h) == 64


def test_signature_round_trip_and_tamper_detection():
    body = b'{"event":"review.approved"}'
    sig = sec.sign("whsec_x", "1760000000", body)
    assert sig.startswith("sha256=") and sec.verify_signature("whsec_x", "1760000000", body, sig)
    assert not sec.verify_signature("whsec_x", "1760000000", body + b" ", sig)
    assert not sec.verify_signature("whsec_x", "1760000001", body, sig)
    assert not sec.verify_signature("other", "1760000000", body, sig)


def test_webhook_url_rules():
    ok = sec.check_webhook_url("https://hooks.example.com/greenlight", False, fake_resolver("93.184.216.34"))
    assert ok is None
    assert sec.check_webhook_url("http://hooks.example.com/x", False, fake_resolver("93.184.216.34"))  # http not allowed in production
    assert sec.check_webhook_url("https://user:pw@hooks.example.com/x", False, fake_resolver("93.184.216.34"))
    assert sec.check_webhook_url("ftp://hooks.example.com/x", True)
    for ip in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.9"):
        assert "private" in sec.check_webhook_url("https://evil.example.com/x", False, fake_resolver(ip))
    assert sec.check_webhook_url("http://localhost:9000/hook", True) is None  # development
