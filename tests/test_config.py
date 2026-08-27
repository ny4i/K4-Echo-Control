import textwrap

import pytest

from k4echo.config import BridgeConfig, ConfigError, RadioConfig, WebhookConfig, load

GOOD_SECRET = "k" * 48


def write(tmp_path, body):
    path = tmp_path / "bridge.ini"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_values_are_read_from_the_ini_file(tmp_path, monkeypatch):
    monkeypatch.delenv("K4_TRANSPORT", raising=False)
    monkeypatch.delenv("K4_RADIO_HOST", raising=False)
    path = write(tmp_path, """
        [bridge]
        transport = webhook

        [radio]
        host = 10.0.0.42
        port = 9200

        [webhook]
        port = 8443
        secret = {}
    """.format(GOOD_SECRET))

    config = load(path)
    assert config.transport == "webhook"
    assert config.radio.host == "10.0.0.42"
    assert config.radio.port == 9200
    assert config.webhook.port == 8443
    config.validate()


def test_inline_comments_are_stripped(tmp_path, monkeypatch):
    """bridge.ini.example documents each env var with a trailing `; NAME`."""
    monkeypatch.delenv("K4_TRANSPORT", raising=False)
    monkeypatch.delenv("K4_RADIO_HOST", raising=False)
    monkeypatch.delenv("K4_RADIO_PORT", raising=False)
    path = write(tmp_path, """
        [bridge]
        transport = iot                     ; K4_TRANSPORT

        [radio]
        host = 10.0.0.42                    ; K4_RADIO_HOST
        port = 9200                         ; K4_RADIO_PORT
    """)

    config = load(path)
    assert config.transport == "iot"
    assert config.radio.host == "10.0.0.42"
    assert config.radio.port == 9200


def test_the_shipped_example_parses(monkeypatch):
    """The installer copies bridge.ini.example verbatim; it must load."""
    import os

    for name in ("K4_TRANSPORT", "K4_RADIO_PORT", "K4_WEBHOOK_PATH"):
        monkeypatch.delenv(name, raising=False)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = load(os.path.join(root, "bridge", "bridge.ini.example"))
    assert config.transport == "iot"
    assert config.radio.port == 9200
    assert config.webhook.path == "/command"


def test_the_environment_overrides_the_file(tmp_path, monkeypatch):
    path = write(tmp_path, """
        [radio]
        host = 10.0.0.42
    """)
    monkeypatch.setenv("K4_RADIO_HOST", "192.168.7.7")
    monkeypatch.setenv("K4_RADIO_PORT", "9201")

    config = load(path)
    assert config.radio.host == "192.168.7.7"
    assert config.radio.port == 9201


def test_a_missing_named_config_file_is_an_error():
    with pytest.raises(ConfigError, match="not found"):
        load("/nonexistent/bridge.ini")


def test_topics_default_to_the_thing_name(monkeypatch):
    monkeypatch.setenv("K4_IOT_THING_NAME", "k4-shack")
    monkeypatch.delenv("K4_IOT_TOPIC", raising=False)

    config = load()
    assert config.command_topic == "k4echo/k4-shack/cmd"
    assert config.result_topic == "k4echo/k4-shack/result"


def test_a_custom_topic_keeps_its_result_sibling(monkeypatch):
    monkeypatch.setenv("K4_IOT_THING_NAME", "k4-shack")
    monkeypatch.setenv("K4_IOT_TOPIC", "ham/radios/k4/cmd")

    config = load()
    assert config.result_topic == "ham/radios/k4/result"


def test_a_short_webhook_secret_is_refused():
    config = BridgeConfig(transport="webhook", webhook=WebhookConfig(secret="hunter2"))
    with pytest.raises(ConfigError, match="too short"):
        config.validate()


def test_a_missing_webhook_secret_is_refused():
    config = BridgeConfig(transport="webhook", webhook=WebhookConfig(secret=""))
    with pytest.raises(ConfigError, match="shared secret"):
        config.validate()


def test_half_configured_tls_is_refused():
    config = BridgeConfig(
        transport="webhook",
        webhook=WebhookConfig(secret=GOOD_SECRET, tls_cert="/etc/cert.pem"),
    )
    with pytest.raises(ConfigError, match="tls_cert and tls_key"):
        config.validate()


def test_iot_transport_lists_everything_it_is_missing():
    with pytest.raises(ConfigError, match="endpoint, thing_name, cert, key, root_ca"):
        BridgeConfig(transport="iot").validate()


def test_an_unknown_transport_is_refused():
    with pytest.raises(ConfigError, match="must be 'iot' or 'webhook'"):
        BridgeConfig(transport="smoke-signals").validate()


def test_an_empty_radio_host_is_refused():
    config = BridgeConfig(transport="iot", radio=RadioConfig(host=""))
    with pytest.raises(ConfigError, match="radio host"):
        config.validate()
