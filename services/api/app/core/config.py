"""Application settings read from SSS_ environment variables, the .env file and Docker secrets."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

APP_NAME = "smart-surveillance-system"
APP_VERSION = "0.1.0"
SECRETS_DIR = "/run/secrets"


class Settings(BaseSettings):
    """Every setting of the backend.

    A setting comes from its SSS_ environment variable, the .env file, or a Docker secret file named after the
    variable in lower case under /run/secrets, such as sss_db_password, in that order.

    Attributes:
        lan_bind: Local network address the web app and the TV streams are published on.
        db_host: Postgres host.
        db_port: Postgres port.
        db_name: Postgres database.
        db_user: Postgres user.
        db_password: Postgres password.
        secret_key: Key that signs sessions and encrypts camera passwords.
        admin_username: User created on first start.
        admin_password: Password of that user on first start.
        session_hours: Lifetime of a web session.
        public_url: Base address the links in emails point at, which the mobile app opens as App Links.
        registration_enabled: Whether anyone may create an account.
        access_token_minutes: Lifetime of an access token.
        access_token_key: PEM of the P-256 key that signs access tokens; empty derives one from the secret key.
        refresh_idle_days: Days a refresh token stays valid unused.
        refresh_max_days: Days a device session lasts at most, whatever its refreshes.
        verify_token_hours: Hours an email confirmation link stays valid.
        reset_token_minutes: Minutes a password reset link stays valid.
        invitation_days: Days an invitation stays valid.
        rate_limit_credentials: Sign-in, token and verification attempts one client address may make a minute.
        rate_limit_mail: Requests that send mail, such as sign-ups, one client address may make a minute.
        smtp_host: SMTP server of outgoing mail; empty logs mail instead of sending it.
        smtp_port: SMTP port.
        smtp_username: SMTP login, empty for none.
        smtp_password: SMTP password.
        smtp_starttls: Whether to upgrade the connection with STARTTLS.
        smtp_ssl: Whether to connect with TLS from the start.
        mail_from: Sender of outgoing mail.
        bootstrap_organisation_name: Organisation the existing installation's rows move into.
        bootstrap_site_name: Site the existing installation's rows move into.
        bootstrap_site_kind: Kind of that site.
        connector_code_minutes: Minutes a connector pairing code stays valid.
        connector_poll_seconds: Seconds a connector waits between pairing polls.
        connector_offline_seconds: Seconds without a heartbeat after which a connector counts as offline.
        direct_cameras: Whether the backend may reach cameras on its own network, as a single-site installation does.
        webrtc_stun_urls: Comma-separated STUN servers offered to the apps.
        webrtc_turn_url: TURN server offered to the apps, empty for none.
        webrtc_turn_username: TURN user.
        webrtc_turn_password: TURN password.
        fcm_project_id: Firebase project of FCM HTTP v1 push, empty to turn FCM off.
        fcm_service_account_file: Path of the Firebase service account key file.
        ntfy_url: Base address of the ntfy server offered to the apps, empty to turn ntfy off.
        ntfy_token: Access token for that ntfy server, empty for none.
        go2rtc_url: go2rtc API address.
        transcode_hardware: go2rtc hardware option added to every transcode, empty for none.
        tv_rtsp_username: RTSP user of go2rtc for clients outside its container.
        tv_rtsp_password: RTSP password of go2rtc.
        discovery_subnet: Subnet scanned for cameras.
        monitor_interval_seconds: Interval of the camera status monitor.
        health_interval_seconds: Interval of the network health probes.
        health_retention_days: Days health samples are kept.
        events_dir: Folder of event snapshots and clips.
        events_retention_days: Days events, their face samples and the analytics appearances and journeys are kept.
        counts_retention_days: Days analytics line counts are kept.
        replay_enabled: Whether SD card playback is offered.
        timezone: Time zone of schedules, line counts and area resets.
        analytics_url: Address of the analytics service, for its status, live objects, view pictures and face embedding.
        analytics_token: Shared token of the analytics service and the internal analytics endpoints.
        nats_url: NATS server of the event bus, or a comma-separated list of servers.
        device_username: Device login of bridged V380 cameras.
        device_password: Device password of bridged V380 cameras.
        device_onvif_port: ONVIF port of bridged V380 cameras on their own address.
        audit_retention_days: Days audit records are kept.
        telegram_bot_token: Token of the Telegram bot that sends alerts; empty turns Telegram alerts off.
        telegram_bot_username: Username of that bot, read from Telegram at start when empty.
        telegram_api_url: Base address of the Telegram Bot API, replaced by a fake in tests and the kit.
        telegram_webhook_secret: Secret Telegram sends with every webhook call, required for webhook mode.
        telegram_webhook_url: Address Telegram delivers updates to; empty uses the public address.
        telegram_polling: Whether to poll Telegram for updates instead of receiving them by webhook.
        telegram_link_minutes: Minutes a chat link stays valid.
    """

    model_config = SettingsConfigDict(
        env_prefix="SSS_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        secrets_dir=SECRETS_DIR if Path(SECRETS_DIR).is_dir() else None,
    )

    lan_bind: str = ""

    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "surveillance"
    db_user: str = "surveillance"
    db_password: str = "surveillance"

    secret_key: str = ""
    admin_username: str = "admin"
    admin_password: str = ""
    session_hours: int = 168

    public_url: str = "http://127.0.0.1:8750"
    registration_enabled: bool = True

    access_token_minutes: int = 15
    access_token_key: str = ""
    refresh_idle_days: int = 30
    refresh_max_days: int = 365
    verify_token_hours: int = 48
    reset_token_minutes: int = 60
    invitation_days: int = 7

    rate_limit_credentials: int = 20
    rate_limit_mail: int = 5

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    mail_from: str = "Smart Surveillance <no-reply@localhost>"

    bootstrap_organisation_name: str = "Home"
    bootstrap_site_name: str = "Home"
    bootstrap_site_kind: str = "home"

    connector_code_minutes: int = 15
    connector_poll_seconds: int = 5
    connector_offline_seconds: int = 90
    direct_cameras: bool = True

    webrtc_stun_urls: str = ""
    webrtc_turn_url: str = ""
    webrtc_turn_username: str = ""
    webrtc_turn_password: str = ""

    fcm_project_id: str = ""
    fcm_service_account_file: str = ""
    ntfy_url: str = ""
    ntfy_token: str = ""

    go2rtc_url: str = "http://go2rtc:1984"
    transcode_hardware: str = ""

    tv_rtsp_username: str = "tv"
    tv_rtsp_password: str = ""

    discovery_subnet: str = "192.168.1.0/24"

    monitor_interval_seconds: int = 20

    health_interval_seconds: int = 10
    health_retention_days: int = 14

    events_dir: str = "/data/events"
    events_retention_days: int = 30
    counts_retention_days: int = 400

    replay_enabled: bool = False

    timezone: str = "UTC"

    analytics_url: str = "http://analytics:8080"
    analytics_token: str = ""

    nats_url: str = "nats://nats:4222"

    device_username: str = Field(default="admin", validation_alias=AliasChoices("sss_device_username", "v380_username"))
    device_password: str = Field(default="", validation_alias=AliasChoices("sss_device_password", "v380_password"))
    device_onvif_port: int = 8899

    audit_retention_days: int = 365

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_api_url: str = "https://api.telegram.org"
    telegram_webhook_secret: str = ""
    telegram_webhook_url: str = ""
    telegram_polling: bool = False
    telegram_link_minutes: int = 30

    @property
    def database_url(self) -> str:
        """str: Async SQLAlchemy URL of the database, with the password."""
        url = URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )
        return url.render_as_string(hide_password=False)


settings = Settings()
