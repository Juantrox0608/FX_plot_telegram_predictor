import io
import json
import urllib.error

import probar_telegram as pt


def test_send_ok_hides_token(monkeypatch):
    seen = {}
    def fake(url, data, timeout, context=None):
        seen["url"], seen["data"] = url, data
        return io.BytesIO(json.dumps({"ok": True}).encode())
    monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
    ok, msg = pt.send("SECRETO", 42, "hola")
    assert ok and "SECRETO" not in msg and b"chat_id=42" in seen["data"]


def test_send_explains_common_errors(monkeypatch):
    for code in (401, 400, 403):
        def fake(url, data, timeout, context=None, code=code):
            raise urllib.error.HTTPError(url, code, "x", {}, None)
        monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
        ok, msg = pt.send("SECRETO", 42, "hola")
        assert not ok and msg == pt.HINTS[code] and "SECRETO" not in msg


def test_send_without_network(monkeypatch):
    def fake(url, data, timeout, context=None):
        raise urllib.error.URLError("sin red")
    monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
    ok, msg = pt.send("SECRETO", 42, "hola")
    assert not ok and "Sin conexión" in msg


def test_error_reason_never_contains_token(monkeypatch):
    def fake(url, data, timeout, context=None):
        raise urllib.error.URLError("CERTIFICATE_VERIFY_FAILED")
    monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
    ok, msg = pt.send("SECRETO", 42, "hola")
    assert not ok and "CERTIFICATE_VERIFY_FAILED" in msg and "SECRETO" not in msg
