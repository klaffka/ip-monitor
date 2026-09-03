import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests

import ip_monitor


class Response:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.data


class ProviderTests(unittest.TestCase):
    def test_primary_global_ipv4(self):
        with patch.object(ip_monitor.requests, "get", return_value=Response({"ip": "8.8.8.8"})):
            self.assertEqual(ip_monitor.fetch_public_ip(False), ("8.8.8.8", []))

    def test_fallback_after_timeout(self):
        responses = [requests.Timeout("zu langsam"), Response({"ip": "1.1.1.1"})]
        with patch.object(ip_monitor.requests, "get", side_effect=responses) as get:
            self.assertEqual(ip_monitor.fetch_public_ip(False), ("1.1.1.1", []))
        self.assertEqual(get.call_count, 2)

    def test_invalid_and_wrong_family_are_rejected(self):
        for first in ("999.1.1.1", "2001:4860:4860::8888"):
            with (
                self.subTest(first=first),
                patch.object(
                    ip_monitor.requests,
                    "get",
                    side_effect=[Response({"ip": first}), Response({"ip": "8.8.4.4"})],
                ),
            ):
                self.assertEqual(ip_monitor.fetch_public_ip(False), ("8.8.4.4", []))

    def test_non_global_is_only_last_candidate(self):
        with patch.object(
            ip_monitor.requests,
            "get",
            side_effect=[Response({"ip": "192.168.1.4"}), Response({"ip": "10.0.0.2"})],
        ):
            address, warnings = ip_monitor.fetch_public_ip(False)
        self.assertEqual(address, "192.168.1.4")
        self.assertIn("nicht global routbar", warnings[0])

    def test_global_provider_beats_non_global_candidate(self):
        with patch.object(
            ip_monitor.requests,
            "get",
            side_effect=[Response({"ip": "192.168.1.4"}), Response({"ip": "8.8.8.8"})],
        ):
            self.assertEqual(ip_monitor.fetch_public_ip(False), ("8.8.8.8", []))

    def test_missing_ipv6_is_distinct_from_ipv4(self):
        def result(is_ipv6):
            return (None, []) if is_ipv6 else ("8.8.8.8", [])

        with patch.object(ip_monitor, "fetch_public_ip", side_effect=result):
            self.assertEqual(ip_monitor.get_ips(), ("8.8.8.8", None, []))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = os.path.join(self.temp_dir.name, "data", "history.json")
        self.file_patch = patch.object(ip_monitor, "IP_FILE", self.history_file)
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def record(self, ipv4="8.8.8.8", ipv6=None, warnings=()):
        with patch.object(ip_monitor, "fetch_ip_info", return_value=("ISP", "Berlin, DE")):
            return ip_monitor.check_and_record(ipv4, ipv6, warnings)

    def test_change_no_change_and_ipv6_change(self):
        self.assertTrue(self.record()[0])
        self.assertEqual(self.record(), (False, None))
        self.assertTrue(self.record(ipv6="2001:4860:4860::8888")[0])
        self.assertEqual(len(ip_monitor.load_history()), 2)

    def test_history_limit(self):
        with patch.object(ip_monitor, "HISTORY_LIMIT", 2):
            self.record("8.8.8.8")
            self.record("1.1.1.1")
            self.record("9.9.9.9")
        self.assertEqual(
            [item["ipv4"] for item in ip_monitor.load_history()], ["1.1.1.1", "9.9.9.9"]
        )

    def test_legacy_cgnat_fields_are_migrated(self):
        os.makedirs(os.path.dirname(self.history_file))
        with open(self.history_file, "w", encoding="utf-8") as file_handle:
            json.dump(
                [
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "ipv4": "100.64.0.1",
                        "ipv6": "Nicht verfügbar",
                        "cgnat": True,
                        "cgnat_reasons": ["Alt"],
                        "isp": None,
                        "location": None,
                    }
                ],
                file_handle,
            )
        entry = ip_monitor.load_history()[0]
        self.assertEqual(entry["warning_reasons"], ["Alt"])
        self.assertNotIn("cgnat", entry)

    def test_corrupt_json_is_not_overwritten(self):
        os.makedirs(os.path.dirname(self.history_file))
        content = "{kaputt"
        with open(self.history_file, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        with self.assertRaises(ip_monitor.HistoryError):
            self.record()
        with open(self.history_file, encoding="utf-8") as file_handle:
            self.assertEqual(file_handle.read(), content)

    def test_invalid_schema_is_rejected(self):
        os.makedirs(os.path.dirname(self.history_file))
        with open(self.history_file, "w", encoding="utf-8") as file_handle:
            json.dump([{"timestamp": "wrong", "ipv4": 4}], file_handle)
        with self.assertRaises(ip_monitor.HistoryError):
            ip_monitor.load_history()

    def test_failed_atomic_replace_keeps_original(self):
        self.record()
        with open(self.history_file, encoding="utf-8") as file_handle:
            original = file_handle.read()
        with patch.object(ip_monitor.os, "replace", side_effect=OSError("voll")):
            with self.assertRaises(ip_monitor.HistoryError):
                self.record("1.1.1.1")
        with open(self.history_file, encoding="utf-8") as file_handle:
            self.assertEqual(file_handle.read(), original)


class IpInfoTests(unittest.TestCase):
    def test_legacy_schema_without_token_and_no_coordinates(self):
        data = {
            "org": "AS1 Provider & Co",
            "city": "Köln",
            "region": "NRW",
            "country": "DE",
            "loc": "50.0,6.0",
        }
        with (
            patch.object(ip_monitor, "IPINFO_TOKEN", None),
            patch.object(ip_monitor.requests, "get", return_value=Response(data)) as get,
        ):
            self.assertEqual(
                ip_monitor.fetch_ip_info("8.8.8.8"),
                ("AS1 Provider & Co", "Köln, NRW, DE"),
            )
        get.assert_called_once_with("https://ipinfo.io/8.8.8.8/json", params=None, timeout=5)

    def test_current_schema_with_token(self):
        with (
            patch.object(ip_monitor, "IPINFO_TOKEN", "secret"),
            patch.object(
                ip_monitor.requests,
                "get",
                return_value=Response({"as": {"name": "Example ISP"}, "country": "DE"}),
            ) as get,
        ):
            self.assertEqual(ip_monitor.fetch_ip_info("1.1.1.1"), ("Example ISP", "DE"))
        self.assertEqual(get.call_args.kwargs["params"], {"token": "secret"})

    def test_lookup_failure_is_noncritical(self):
        with patch.object(ip_monitor.requests, "get", side_effect=requests.Timeout()):
            self.assertEqual(ip_monitor.fetch_ip_info("8.8.8.8"), (None, None))

    def test_coordinates_are_never_used_as_location(self):
        with patch.object(
            ip_monitor.requests,
            "get",
            return_value=Response({"org": "ISP", "loc": "50.0,6.0"}),
        ):
            self.assertEqual(ip_monitor.fetch_ip_info("8.8.8.8"), ("ISP", None))


class MessageAndAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_html_is_escaped(self):
        text = ip_monitor.build_ip_message(
            "8.8.8.8",
            None,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            changed=True,
            warning_reasons=["<verdächtig> & falsch"],
            isp="A&B <ISP>",
            location="Berlin > Potsdam",
        )
        self.assertNotIn("<ISP>", text)
        self.assertIn("A&amp;B &lt;ISP&gt;", text)
        self.assertIn("&lt;verdächtig&gt; &amp; falsch", text)

    async def test_unauthorized_chat_gets_no_reply(self):
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=999),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        with patch.object(ip_monitor, "CHAT_IDS", [123]):
            await ip_monitor.handle_start(update, None)
        update.message.reply_text.assert_not_awaited()

    async def test_send_uses_html(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(ip_monitor, "CHAT_IDS", [1]):
            await ip_monitor.send_to_chats(SimpleNamespace(bot=bot), "<b>ok</b>")
        bot.send_message.assert_awaited_once_with(chat_id=1, text="<b>ok</b>", parse_mode="HTML")


class WebhookTests(unittest.TestCase):
    def test_reverse_proxy_allows_arbitrary_valid_port(self):
        self.assertEqual(
            ip_monitor.validate_webhook_config("https://bot.example/hook", None, None, 9000),
            ("hook", False),
        )

    def test_direct_tls_uses_telegram_ports(self):
        self.assertEqual(
            ip_monitor.validate_webhook_config("https://bot.example", "cert", "key", 8443),
            ("telegram", True),
        )
        with self.assertRaises(ValueError):
            ip_monitor.validate_webhook_config("https://bot.example", "cert", "key", 9000)

    def test_invalid_partial_and_public_url_configs(self):
        bad = [
            ("http://bot.example", None, None, 8000),
            ("https:///hook", None, None, 8000),
            ("https://bot.example", "cert", None, 443),
            ("https://bot.example", None, "key", 443),
            ("https://bot.example", None, None, 65536),
        ]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ip_monitor.validate_webhook_config(*values)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_does_not_check_ip(self):
        with (
            patch.object(ip_monitor, "write_heartbeat") as heartbeat,
            patch.object(ip_monitor, "get_ips") as get_ips,
            patch.object(
                ip_monitor.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await ip_monitor._heartbeat_loop(None)
        heartbeat.assert_called_once_with()
        get_ips.assert_not_called()

    async def test_shutdown_cancels_and_awaits_heartbeat(self):
        task = asyncio.create_task(asyncio.sleep(60))
        await ip_monitor.post_shutdown(SimpleNamespace(bot_data={"heartbeat_task": task}))
        self.assertTrue(task.cancelled())

    async def test_post_init_registers_commands(self):
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        app = SimpleNamespace(bot=bot, bot_data={})
        with (
            patch.object(ip_monitor, "write_heartbeat"),
            patch.object(ip_monitor, "check_initial_ip", AsyncMock()),
            patch.object(ip_monitor, "_heartbeat_loop", AsyncMock()),
        ):
            await ip_monitor.post_init(app)
            await asyncio.sleep(0)
            app.bot_data["heartbeat_task"].cancel()
        commands = bot.set_my_commands.await_args.args[0]
        self.assertIn("help", [command.command for command in commands])


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_hides_webhook_url_and_shows_attempt_success_error(self):
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=123),
            message=SimpleNamespace(reply_text=reply),
        )
        instant = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with (
            patch.object(ip_monitor, "CHAT_IDS", [123]),
            patch.object(ip_monitor, "WEBHOOK_URL", "https://secret.example/hook"),
            patch.object(ip_monitor, "LAST_ATTEMPT_AT", instant),
            patch.object(ip_monitor, "LAST_SUCCESS_AT", instant),
            patch.object(ip_monitor, "LAST_ERROR", "Fehler <intern>"),
            patch.object(ip_monitor, "load_history", return_value=[]),
        ):
            await ip_monitor.handle_status(update, None)
        text = reply.await_args.args[0]
        self.assertIn("Letzter Versuch", text)
        self.assertIn("Letzter Erfolg", text)
        self.assertIn("Fehler &lt;intern&gt;", text)
        self.assertNotIn("secret.example", text)


class CheckLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_checks_are_serialized(self):
        active = 0
        maximum = 0

        def get_ips():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            import time

            time.sleep(0.02)
            active -= 1
            return "8.8.8.8", None, []

        with (
            patch.object(ip_monitor, "CHECK_LOCK", asyncio.Lock()),
            patch.object(ip_monitor, "get_ips", side_effect=get_ips),
            patch.object(ip_monitor, "check_and_record", return_value=(False, None)),
        ):
            await asyncio.gather(ip_monitor.perform_check(), ip_monitor.perform_check())
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
