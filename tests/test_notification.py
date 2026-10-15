"""PushPlus request contract, failure handling, and channel selection."""

import io
import json
from datetime import datetime
from urllib.error import HTTPError, URLError

import pytest
from hydra import compose, initialize_config_dir
from hydra.core.global_hydra import GlobalHydra
from omegaconf import OmegaConf

from tests.conftest import _CONFIG_DIR
from zotero_arxiv_daily.notification import send_notification, send_pushplus


def test_pushplus_html_request(config, monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(b'{"code":200,"data":"message-id"}')

    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", fake_urlopen)
    html = "<h1>今日论文</h1>"
    assert send_pushplus(config, html) == "message-id"
    request, timeout = calls[0]
    assert request.full_url == "https://www.pushplus.plus/send"
    assert request.method == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert timeout == 30
    assert json.loads(request.data) == {
        "token": "fake-pushplus-token",
        "title": f"Daily arXiv {datetime.now():%Y/%m/%d}",
        "content": html,
        "template": "html",
        "channel": "wechat",
    }


def test_pushplus_group(config, monkeypatch):
    config.pushplus.topic = "research"
    config.pushplus.timeout = 12

    def fake_urlopen(request, timeout):
        assert json.loads(request.data)["topic"] == "research"
        assert timeout == 12
        return io.BytesIO(b'{"code":200,"data":"group-id"}')

    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", fake_urlopen)
    assert send_pushplus(config, "digest") == "group-id"


@pytest.mark.parametrize("token", [None, "", "  "])
def test_pushplus_requires_token(config, monkeypatch, token):
    config.pushplus.token = token
    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", lambda *a, **kw: pytest.fail("Unexpected request"))
    with pytest.raises(ValueError, match="PUSHPLUS_TOKEN"):
        send_pushplus(config, "digest")


@pytest.mark.parametrize("body", [b'{"code":600,"msg":"fake-pushplus-token"}', b'{}', b'[]'])
def test_pushplus_rejection(config, monkeypatch, body):
    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", lambda *a, **kw: io.BytesIO(body))
    with pytest.raises(RuntimeError, match="rejected") as exc:
        send_pushplus(config, "digest")
    assert config.pushplus.token not in str(exc.value)


def test_pushplus_invalid_response(config, monkeypatch):
    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", lambda *a, **kw: io.BytesIO(b'<html>Error</html>'))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        send_pushplus(config, "digest")


def test_pushplus_validation_error_reports_message_without_token(config, monkeypatch):
    body = json.dumps({
        "code": 999,
        "msg": f"Validation failed for {config.pushplus.token}: content too long",
        "data": "private response data",
    }).encode("utf-8")
    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", lambda *a, **kw: io.BytesIO(body))
    with pytest.raises(RuntimeError, match="code 999") as exc:
        send_pushplus(config, "digest")
    assert "content too long" in str(exc.value)
    assert "[REDACTED]" in str(exc.value)
    assert config.pushplus.token not in str(exc.value)
    assert "private response data" not in str(exc.value)


@pytest.mark.parametrize("error", [
    HTTPError("https://www.pushplus.plus/send", 503, "unavailable", {}, None),
    URLError("fake-pushplus-token"),
    TimeoutError(),
])
def test_pushplus_network_failure(config, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr("zotero_arxiv_daily.notification.urlopen", fail)
    with pytest.raises(RuntimeError, match="PushPlus") as exc:
        send_pushplus(config, "digest")
    assert config.pushplus.token not in str(exc.value)


def test_pushplus_dispatch_does_not_require_email(config, monkeypatch):
    del config.email
    calls = []
    monkeypatch.setattr("zotero_arxiv_daily.notification.send_pushplus", lambda cfg, html: calls.append(html))
    send_notification(config, "digest")
    assert calls == ["digest"]


def test_unknown_channel(config):
    config.notification.channel = "unknown"
    with pytest.raises(ValueError, match="Unsupported notification channel"):
        send_notification(config, "digest")


def test_default_config_uses_pushplus_env_without_smtp(monkeypatch):
    for name in ("SENDER", "RECEIVER", "SENDER_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PUSHPLUS_TOKEN", "env-pushplus-token")
    GlobalHydra.instance().clear()
    with initialize_config_dir(config_dir=_CONFIG_DIR, version_base=None):
        cfg = compose(config_name="default")
    assert cfg.notification.channel == "pushplus"
    assert cfg.pushplus.token == "env-pushplus-token"
    assert OmegaConf.to_container(cfg.pushplus, resolve=True)["timeout"] == 30
