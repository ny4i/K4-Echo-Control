"""Configuration loading for the home bridge.

Values come from an INI file, and any of them can be overridden by an
environment variable so that secrets and certificate paths need not live in
the file itself (useful for systemd ``EnvironmentFile=`` or a Windows service).
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_CONFIG_PATHS = (
    os.environ.get("K4_BRIDGE_CONFIG", ""),
    "/etc/k4echo/bridge.ini",
    os.path.expanduser("~/.config/k4echo/bridge.ini"),
    "bridge.ini",
)


class ConfigError(Exception):
    """Raised when the bridge is misconfigured."""


@dataclass
class RadioConfig:
    host: str = "192.168.1.50"
    port: int = 9200
    connect_timeout: float = 5.0
    reply_timeout: float = 3.0


@dataclass
class WebhookConfig:
    bind: str = "0.0.0.0"
    port: int = 8443
    path: str = "/command"
    secret: str = ""
    max_skew: int = 300
    tls_cert: str = ""
    tls_key: str = ""


@dataclass
class IotConfig:
    endpoint: str = ""
    thing_name: str = ""
    cert: str = ""
    key: str = ""
    root_ca: str = ""
    topic: str = ""
    client_id: str = ""
    shadow_interval: int = 300


@dataclass
class BridgeConfig:
    transport: str = "iot"
    log_level: str = "INFO"
    allow_raw_cat: bool = False
    radio: RadioConfig = field(default_factory=RadioConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    iot: IotConfig = field(default_factory=IotConfig)

    def validate(self) -> None:
        if self.transport not in ("iot", "webhook"):
            raise ConfigError(
                "transport must be 'iot' or 'webhook', not {!r}".format(self.transport)
            )

        if not self.radio.host:
            raise ConfigError("radio host is not set")

        if self.transport == "webhook":
            if not self.webhook.secret:
                raise ConfigError(
                    "webhook transport needs a shared secret "
                    "(set [webhook] secret, or the K4_BRIDGE_SECRET environment variable)"
                )
            if len(self.webhook.secret) < 32:
                raise ConfigError(
                    "the webhook secret is too short; use at least 32 characters "
                    "(tools/make_secret.py generates one)"
                )
            if bool(self.webhook.tls_cert) != bool(self.webhook.tls_key):
                raise ConfigError("set both tls_cert and tls_key, or neither")

        if self.transport == "iot":
            missing = [
                field_name
                for field_name in ("endpoint", "thing_name", "cert", "key", "root_ca")
                if not getattr(self.iot, field_name)
            ]
            if missing:
                raise ConfigError(
                    "iot transport is missing: {}".format(", ".join(missing))
                )

    @property
    def command_topic(self) -> str:
        return self.iot.topic or "k4echo/{}/cmd".format(self.iot.thing_name)

    @property
    def result_topic(self) -> str:
        return self.command_topic.rsplit("/", 1)[0] + "/result"


def _get(parser: configparser.ConfigParser, section: str, option: str, env: str, fallback):
    """Resolve one option: environment variable wins, then INI, then default."""
    override = os.environ.get(env)
    if override not in (None, ""):
        return override
    if parser.has_option(section, option):
        return parser.get(section, option)
    return fallback


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def find_config_path(explicit: Optional[str] = None) -> Optional[str]:
    """Return the first config file that exists."""
    candidates = (explicit,) + DEFAULT_CONFIG_PATHS if explicit else DEFAULT_CONFIG_PATHS
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def load(path: Optional[str] = None) -> BridgeConfig:
    """Load bridge configuration from ``path`` (or the first default that exists)."""
    parser = configparser.ConfigParser()
    resolved = find_config_path(path)

    if path and not resolved:
        raise ConfigError("config file not found: {}".format(path))
    if resolved:
        parser.read(resolved)

    config = BridgeConfig(
        transport=str(_get(parser, "bridge", "transport", "K4_TRANSPORT", "iot")).strip().lower(),
        log_level=str(_get(parser, "bridge", "log_level", "K4_LOG_LEVEL", "INFO")).strip().upper(),
        allow_raw_cat=_as_bool(_get(parser, "bridge", "allow_raw_cat", "K4_ALLOW_RAW_CAT", False)),
        radio=RadioConfig(
            host=str(_get(parser, "radio", "host", "K4_RADIO_HOST", RadioConfig.host)).strip(),
            port=int(_get(parser, "radio", "port", "K4_RADIO_PORT", RadioConfig.port)),
            connect_timeout=float(
                _get(parser, "radio", "connect_timeout", "K4_RADIO_CONNECT_TIMEOUT", RadioConfig.connect_timeout)
            ),
            reply_timeout=float(
                _get(parser, "radio", "reply_timeout", "K4_RADIO_REPLY_TIMEOUT", RadioConfig.reply_timeout)
            ),
        ),
        webhook=WebhookConfig(
            bind=str(_get(parser, "webhook", "bind", "K4_WEBHOOK_BIND", WebhookConfig.bind)).strip(),
            port=int(_get(parser, "webhook", "port", "K4_WEBHOOK_PORT", WebhookConfig.port)),
            path=str(_get(parser, "webhook", "path", "K4_WEBHOOK_PATH", WebhookConfig.path)).strip(),
            secret=str(_get(parser, "webhook", "secret", "K4_BRIDGE_SECRET", "")).strip(),
            max_skew=int(_get(parser, "webhook", "max_skew", "K4_WEBHOOK_MAX_SKEW", WebhookConfig.max_skew)),
            tls_cert=str(_get(parser, "webhook", "tls_cert", "K4_WEBHOOK_TLS_CERT", "")).strip(),
            tls_key=str(_get(parser, "webhook", "tls_key", "K4_WEBHOOK_TLS_KEY", "")).strip(),
        ),
        iot=IotConfig(
            endpoint=str(_get(parser, "iot", "endpoint", "K4_IOT_ENDPOINT", "")).strip(),
            thing_name=str(_get(parser, "iot", "thing_name", "K4_IOT_THING_NAME", "")).strip(),
            cert=str(_get(parser, "iot", "cert", "K4_IOT_CERT", "")).strip(),
            key=str(_get(parser, "iot", "key", "K4_IOT_KEY", "")).strip(),
            root_ca=str(_get(parser, "iot", "root_ca", "K4_IOT_ROOT_CA", "")).strip(),
            topic=str(_get(parser, "iot", "topic", "K4_IOT_TOPIC", "")).strip(),
            client_id=str(_get(parser, "iot", "client_id", "K4_IOT_CLIENT_ID", "")).strip(),
            shadow_interval=int(
                _get(parser, "iot", "shadow_interval", "K4_IOT_SHADOW_INTERVAL", IotConfig.shadow_interval)
            ),
        ),
    )

    config.source_path = resolved  # type: ignore[attr-defined]
    return config
