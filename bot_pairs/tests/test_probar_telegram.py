import io
import json
import urllib.error

import probar_telegram as pt


def test_send_ok_hides_token(monkeypatch):
    seen = {}
    def fake(url, data, timeout):
        seen["url"], seen["data"] = url, data
        return io.BytesIO(json.dumps({"ok": True}).encode())
    monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
    ok, msg = pt.send("SECRETO", 42, "hola")
    assert ok and "SECRETO" not in msg and b"chat_id=42" in seen["data"]


def test_send_explains_common_errors(monkeypatch):
    for code in (401, 400, 403):
        def fake(url, data, timeout, code=code):
            raise urllib.error.HTTPError(url, code, "x", {}, None)
        monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
        ok, msg = pt.send("SECRETO", 42, "hola")
        assert not ok and msg == pt.HINTS[code] and "SECRETO" not in msg


def test_send_without_network(monkeypatch):
    def fake(url, data, timeout):
        raise urllib.error.URLError("sin red")
    monkeypatch.setattr(pt.urllib.request, "urlopen", fake)
    ok, msg = pt.send("SECRETO", 42, "hola")
    assert not ok and "Sin conexión" in msg
