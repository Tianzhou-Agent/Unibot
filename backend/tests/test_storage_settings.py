from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from tianzhou_agent_platform.store.settings import (
    DEFAULT_MAX_PAGE_SIZE,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_PAGE_SIZE,
    MySQLAdapterSettings,
    NASAdapterSettings,
    RedisAdapterSettings,
    S3AdapterSettings,
    StorageSettings,
)


class StorageSettingsTests(unittest.TestCase):
    def test_defaults_with_minimal_adapter_configuration(self) -> None:
        settings = StorageSettings(
            adapters={
                "mysql": {
                    "type": "mysql",
                    "url": "mysql+aiomysql://user:pass@localhost/db",
                },
            },
        )

        self.assertEqual(settings.routes, {})
        self.assertIsNone(settings.default_adapter)
        self.assertEqual(settings.default_timeout_seconds, 5.0)
        self.assertEqual(settings.max_payload_bytes, DEFAULT_MAX_PAYLOAD_BYTES)
        self.assertEqual(settings.default_page_size, DEFAULT_PAGE_SIZE)
        self.assertEqual(settings.max_page_size, DEFAULT_MAX_PAGE_SIZE)
        self.assertIsInstance(settings.adapters["mysql"], MySQLAdapterSettings)

    def test_adapter_specific_settings_are_parsed(self) -> None:
        settings = StorageSettings(
            adapters={
                "mysql": {
                    "type": "mysql",
                    "url": "mysql+aiomysql://user:pass@localhost/db",
                    "ssl_ca": "ca.pem",
                    "ssl_cert": "client.pem",
                    "ssl_key": "secret-key",
                },
                "redis": {
                    "type": "redis",
                    "url": "rediss://localhost:6379/0",
                    "ssl": True,
                    "ssl_ca_certs": "redis-ca.pem",
                    "ssl_certfile": "redis-client.pem",
                    "ssl_keyfile": "redis-key",
                },
                "attachments": {
                    "type": "s3",
                    "bucket": "unibot",
                    "server_side_encryption": "aws:kms",
                    "kms_key_id": "key-id",
                },
                "local_files": {
                    "type": "nas",
                    "root_path": "C:/storage/unibot",
                },
            },
            routes={
                "cache": "redis",
                "files": "attachments",
            },
            default_adapter="mysql",
        )

        self.assertIsInstance(settings.adapters["redis"], RedisAdapterSettings)
        self.assertIsInstance(settings.adapters["attachments"], S3AdapterSettings)
        self.assertIsInstance(settings.adapters["local_files"], NASAdapterSettings)
        s3_settings = settings.adapters["attachments"]
        nas_settings = settings.adapters["local_files"]
        self.assertIsInstance(s3_settings, S3AdapterSettings)
        self.assertIsInstance(nas_settings, NASAdapterSettings)
        self.assertEqual(s3_settings.server_side_encryption, "aws:kms")
        self.assertEqual(s3_settings.kms_key_id, "key-id")
        self.assertIsInstance(nas_settings.root_path, Path)

    def test_invalid_route_referencing_missing_adapter_fails(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(
                adapters={
                    "mysql": {
                        "type": "mysql",
                        "url": "mysql+aiomysql://user:pass@localhost/db",
                    },
                },
                routes={"cache": "redis"},
            )

    def test_invalid_route_namespace_fails(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(
                adapters={
                    "mysql": {
                        "type": "mysql",
                        "url": "mysql+aiomysql://user:pass@localhost/db",
                    },
                },
                routes={"Bad Namespace": "mysql"},
            )

    def test_missing_required_adapter_configuration_fails(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(adapters={"mysql": {"type": "mysql"}})

    def test_adapter_settings_forbid_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(
                adapters={
                    "mysql": {
                        "type": "mysql",
                        "url": "mysql+aiomysql://user:pass@localhost/db",
                        "urll": "typo",
                    },
                },
            )

    def test_unknown_adapter_type_fails(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(
                adapters={
                    "database": {
                        "type": "postgres",
                        "url": "postgresql://user:pass@localhost/db",
                    },
                },
            )

    def test_missing_adapter_map_fails(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings()

    def test_default_adapter_must_be_configured(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(
                adapters={
                    "mysql": {
                        "type": "mysql",
                        "url": "mysql+aiomysql://user:pass@localhost/db",
                    },
                },
                default_adapter="redis",
            )

    def test_page_size_defaults_must_not_exceed_maximum(self) -> None:
        with self.assertRaises(ValidationError):
            StorageSettings(
                adapters={
                    "mysql": {
                        "type": "mysql",
                        "url": "mysql+aiomysql://user:pass@localhost/db",
                    },
                },
                default_page_size=101,
                max_page_size=100,
            )

    def test_numeric_setting_constraints(self) -> None:
        adapter_config = {
            "mysql": {
                "type": "mysql",
                "url": "mysql+aiomysql://user:pass@localhost/db",
            },
        }

        with self.assertRaises(ValidationError):
            StorageSettings(adapters=adapter_config, default_timeout_seconds=0)

        with self.assertRaises(ValidationError):
            StorageSettings(adapters=adapter_config, max_payload_bytes=-1)

    def test_environment_loading(self) -> None:
        env = {
            "TZAP_STORAGE_ADAPTERS": '{"mysql":{"type":"mysql","url":"mysql+aiomysql://user:pass@localhost/db"}}',
            "TZAP_STORAGE_ROUTES": '{"memory":"mysql"}',
            "TZAP_STORAGE_DEFAULT_ADAPTER": "mysql",
        }

        with patch.dict(os.environ, env, clear=False):
            settings = StorageSettings()

        self.assertEqual(settings.routes, {"memory": "mysql"})
        self.assertEqual(settings.default_adapter, "mysql")
        self.assertIsInstance(settings.adapters["mysql"], MySQLAdapterSettings)

    def test_nested_environment_loading(self) -> None:
        env = {
            "TZAP_STORAGE_ADAPTERS__MYSQL__TYPE": "mysql",
            "TZAP_STORAGE_ADAPTERS__MYSQL__URL": "mysql+aiomysql://user:pass@localhost/db",
            "TZAP_STORAGE_ROUTES__MEMORY": "mysql",
            "TZAP_STORAGE_DEFAULT_ADAPTER": "mysql",
        }

        with patch.dict(os.environ, env, clear=False):
            settings = StorageSettings()

        self.assertEqual(settings.routes, {"memory": "mysql"})
        self.assertEqual(settings.default_adapter, "mysql")
        self.assertIsInstance(settings.adapters["mysql"], MySQLAdapterSettings)

    def test_secrets_are_masked_in_repr(self) -> None:
        settings = StorageSettings(
            adapters={
                "mysql": {
                    "type": "mysql",
                    "url": "mysql+aiomysql://user:super-secret@localhost/db",
                    "ssl_key": "private-key",
                },
                "s3": {
                    "type": "s3",
                    "bucket": "unibot",
                    "access_key_id": "access-key",
                    "secret_access_key": "secret-key",
                    "session_token": "session-token",
                },
            },
            default_adapter="mysql",
        )

        rendered_settings = repr(settings)
        rendered_mysql = str(settings.adapters["mysql"])
        rendered_s3 = str(settings.adapters["s3"])

        for secret in [
            "super-secret",
            "private-key",
            "access-key",
            "secret-key",
            "session-token",
        ]:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, rendered_settings)
                self.assertNotIn(secret, rendered_mysql)
                self.assertNotIn(secret, rendered_s3)

    def test_create_route_resolver_uses_settings(self) -> None:
        settings = StorageSettings(
            adapters={
                "mysql": {
                    "type": "mysql",
                    "url": "mysql+aiomysql://user:pass@localhost/db",
                },
                "redis": {
                    "type": "redis",
                    "url": "redis://localhost:6379/0",
                },
            },
            routes={"cache": "redis"},
            default_adapter="mysql",
        )

        resolver = settings.create_route_resolver()

        self.assertEqual(resolver.resolve("cache").adapter_name, "redis")
        self.assertEqual(resolver.resolve("memory").adapter_name, "mysql")


if __name__ == "__main__":
    unittest.main()
