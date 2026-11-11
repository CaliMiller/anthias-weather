"""Tests against a fake Anthias server and a fake NWS server.

Run with:  python3 -m unittest discover -s tests -v
"""
import base64
import datetime as dt
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import anthias_weather as aw  # noqa: E402

UTC = dt.timezone.utc


def iso(delta_minutes: int) -> str:
    return (dt.datetime.now(UTC) + dt.timedelta(minutes=delta_minutes)).isoformat()


class FakeAnthias:
    def __init__(self, require_auth=False):
        self.require_auth = require_auth
        self.down = False
        self.controls: list[str] = []
        self.patches: list[tuple[str, bool]] = []
        self.orders: list[list[str]] = []
        self.assets = {}
        for i, (name, enabled) in enumerate([
            ("Political Video 1", True), ("Political Video 2", True), ("Political Page", True),
            ("Weather Current", True), ("Weather Forecast", True), ("Weather Hourly", True),
            ("Weather Alerts", True), ("Weather Radar", True),
            ("Old Ad (manually disabled)", False), ("Warning Banner", False),
        ]):
            aid = f"id{i:02d}"
            self.assets[aid] = {"asset_id": aid, "name": name, "is_enabled": enabled,
                                "is_active": enabled, "play_order": i}

    def add(self, aid, name, enabled=True, play_order=99, first=False):
        asset = {"asset_id": aid, "name": name, "is_enabled": enabled, "is_active": enabled,
                 "play_order": play_order}
        if first:  # created before everything else (database order != playlist order)
            self.assets = {aid: asset, **self.assets}
        else:
            self.assets[aid] = asset

    def enabled_names(self):
        return sorted(a["name"] for a in self.assets.values() if a["is_enabled"])

    def playing(self):
        """Enabled asset names in the order the viewer plays them."""
        live = [a for a in self.assets.values() if a["is_enabled"]]
        return [a["name"] for a in sorted(live, key=lambda a: a["play_order"])]

    def finalize_update(self, aid):
        """Mirrors Anthias's finalize_asset_update(): after every PATCH it
        renumbers active assets in *database* order (not play order), then
        inserts the edited asset at its old play_order index."""
        active = [a for a in self.assets if self.assets[a]["is_enabled"]]
        if aid in active:
            active.remove(aid)
        if self.assets[aid]["is_enabled"]:
            active.insert(self.assets[aid]["play_order"], aid)
        for i, a in enumerate(active):
            self.assets[a]["play_order"] = i

    def set_order(self, ids):
        self.orders.append(ids)
        for i, a in enumerate(ids):
            if a in self.assets:
                self.assets[a]["play_order"] = i


class FakeNWS:
    def __init__(self):
        self.features = []
        self.down = False
        self.requests: list[tuple[str, str]] = []

    def add(self, event, minutes=30, status="Actual", message_type="Alert", aid=None):
        self.features.append({"properties": {
            "id": aid or f"urn:test:{event}:{len(self.features)}", "event": event, "status": status,
            "messageType": message_type, "headline": f"{event} for Test County",
            "areaDesc": "Test County", "expires": iso(minutes), "ends": iso(minutes)}})


def make_handler(anthias: FakeAnthias, nws: FakeNWS):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=None, ctype="application/json", headers=None):
            raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def _anthias_ok(self):
            if anthias.down:
                self._send(503, {"error": "down"})
                return False
            if anthias.require_auth:
                expected = "Basic " + base64.b64encode(b"admin:secret").decode()
                if self.headers.get("Authorization") != expected:
                    self._send(302, None, "text/html", {"Location": "/login/?next=" + self.path})
                    return False
            return True

        def do_GET(self):
            path = urllib.parse.urlparse(self.path)
            if path.path.startswith("/alerts/active"):
                nws.requests.append((path.query, self.headers.get("User-Agent")))
                if nws.down:
                    return self._send(500, {"title": "Unexpected Problem"})
                return self._send(200, {"type": "FeatureCollection", "features": nws.features},
                                  "application/geo+json")
            if not self._anthias_ok():
                return
            if path.path == "/api/v2/assets":
                return self._send(200, list(anthias.assets.values()))
            if path.path.startswith("/api/v2/assets/control/"):
                cmd = urllib.parse.unquote(path.path.rsplit("/", 1)[1])
                anthias.controls.append(cmd)
                return self._send(200, "Asset switched")
            self._send(404, {"detail": "not found"})

        def do_PATCH(self):
            if not self._anthias_ok():
                return
            aid = urllib.parse.unquote(self.path.rsplit("/", 1)[1])
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            asset = anthias.assets[aid]
            asset["is_enabled"] = bool(body["is_enabled"])
            asset["is_active"] = asset["is_enabled"]
            anthias.patches.append((aid, asset["is_enabled"]))
            anthias.finalize_update(aid)
            self._send(200, asset)

        def do_POST(self):
            if not self._anthias_ok():
                return
            if urllib.parse.urlparse(self.path).path != "/api/v2/assets/order":
                return self._send(404, {"detail": "not found"})
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            anthias.set_order(body["ids"].split(","))
            self._send(204)
    return Handler


class Base(unittest.TestCase):
    require_auth = False

    def setUp(self):
        self.anthias = FakeAnthias(require_auth=self.require_auth)
        self.nws = FakeNWS()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.anthias, self.nws))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = self.make_config()
        self.rebuild()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def make_config(self, **overrides):
        auth = 'username = "admin"\npassword = "secret"\n' if self.require_auth else ""
        text = f"""
[anthias]
url = "{self.url}"
{auth}
[alert_mode]
assets = ["Weather Alerts", "weather radar"]
switch_delay_seconds = 0

[nws]
point = "39.76838,-86.15804"
user_agent = "anthias-weather-test (tests@localhost)"
api_url = "{self.url}"
poll_seconds = 60
clear_after_polls = 2

[service]
state_dir = "{self.tmp.name}"
"""
        path = os.path.join(self.tmp.name, "config.toml")
        with open(path, "w") as fh:
            fh.write(text)
        cfg = aw.load_config(path)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def rebuild(self):
        """Simulates a service restart: everything reloads from disk."""
        anthias, nws, store = aw.build(self.cfg)
        self.store = store
        self.controller = aw.Controller(self.cfg, anthias, store, sleep=lambda s: None)
        self.daemon = aw.Daemon(self.cfg, self.controller, nws, store)

    NORMAL = sorted(["Political Video 1", "Political Video 2", "Political Page", "Weather Current",
                     "Weather Forecast", "Weather Hourly", "Weather Alerts", "Weather Radar"])
    ALERT = ["Weather Alerts", "Weather Radar"]


class TestBasicFlow(Base):
    def test_config_normalizes_point_and_sends_user_agent(self):
        self.assertEqual(self.cfg.nws_point, "39.7684,-86.158")
        self.daemon.tick(None)
        query, ua = self.nws.requests[-1]
        self.assertEqual(urllib.parse.parse_qs(query)["point"], ["39.7684,-86.158"])
        self.assertEqual(ua, "anthias-weather-test (tests@localhost)")

    def test_quiet_weather_changes_nothing(self):
        self.daemon.tick(None)
        self.assertEqual(self.anthias.patches, [])
        self.assertEqual(self.controller.mode, "normal")

    def test_warning_enters_then_restores_after_two_clear_polls(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)
        self.assertEqual(self.anthias.controls, ["asset&id06"])  # jumped to Weather Alerts

        self.nws.features.clear()
        self.daemon.tick(None)  # first clear poll: hold
        self.assertEqual(self.controller.mode, "alert")
        self.daemon.tick(None)  # second clear poll: restore
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)
        self.assertEqual(self.anthias.controls[-1], "next")
        # Manually disabled assets stayed disabled.
        self.assertFalse(self.anthias.assets["id08"]["is_enabled"])

    def test_one_clear_poll_then_warning_again_does_not_flap(self):
        self.nws.add("Severe Thunderstorm Warning")
        self.daemon.tick(None)
        self.nws.features.clear()
        self.daemon.tick(None)
        self.nws.add("Severe Thunderstorm Warning")
        self.daemon.tick(None)
        self.nws.features.clear()
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")
        patches_while_alert = len(self.anthias.patches)
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(patches_while_alert, 6)  # only the initial 6 disables happened

    def test_alert_page_that_is_normally_disabled(self):
        self.cfg.alert_assets = ["Weather Radar", "Warning Banner"]
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), ["Warning Banner", "Weather Radar"])
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertFalse(self.anthias.assets["id09"]["is_enabled"])
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)


class TestAlertFiltering(Base):
    def assert_ignored(self):
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(self.anthias.patches, [])

    def test_watch_is_ignored(self):
        self.nws.add("Tornado Watch")
        self.assert_ignored()

    def test_test_message_is_ignored(self):
        self.nws.add("Tornado Warning", status="Test")
        self.assert_ignored()

    def test_cancel_message_is_ignored(self):
        self.nws.add("Tornado Warning", message_type="Cancel")
        self.assert_ignored()

    def test_already_expired_is_ignored(self):
        self.nws.add("Tornado Warning", minutes=-5)
        self.assert_ignored()

    def test_update_message_counts(self):
        self.nws.add("Tornado Warning", message_type="Update")
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")

    def test_event_names_are_case_insensitive(self):
        self.cfg.nws_events = ["tornado warning"]
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")


class TestResilience(Base):
    def test_restart_mid_alert_still_restores_original(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.rebuild()  # service restarts / PC reboots
        self.assertEqual(self.controller.mode, "alert")
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)

    def test_restart_mid_alert_reapplies_if_someone_reenabled_assets(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.anthias.assets["id00"]["is_enabled"] = True
        self.rebuild()
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)

    def test_nws_outage_keeps_alert_mode(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.nws.down = True
        for _ in range(5):
            self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)

    def test_nws_outage_exits_once_known_alerts_have_ended(self):
        self.nws.add("Tornado Warning", minutes=10)
        self.daemon.tick(None)
        self.nws.down = True
        self.daemon.now = lambda: dt.datetime.now(UTC) + dt.timedelta(minutes=11)
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)

    def test_nws_outage_in_normal_mode_does_nothing(self):
        self.nws.down = True
        self.daemon.tick(None)
        self.assertEqual(self.anthias.patches, [])

    def test_anthias_down_during_enter_then_recovers(self):
        self.nws.add("Tornado Warning")
        self.anthias.down = True
        with self.assertRaises(aw.AnthiasError):
            self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")  # list failed before anything was saved
        self.anthias.down = False
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)

    def test_anthias_fails_halfway_through_enter(self):
        self.nws.add("Tornado Warning")
        original = self.controller.anthias.set_enabled
        calls = {"n": 0}

        def flaky(aid, enabled):
            calls["n"] += 1
            if calls["n"] == 3:
                raise aw.AnthiasError("simulated failure")
            return original(aid, enabled)
        self.controller.anthias.set_enabled = flaky
        with self.assertRaises(aw.AnthiasError):
            self.daemon.tick(None)
        self.controller.dirty = True  # what run() does on AnthiasError
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)

    def test_anthias_fails_during_restore_then_retries(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.nws.features.clear()
        self.daemon.tick(None)
        self.anthias.down = True
        with self.assertRaises(aw.AnthiasError):
            self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")  # snapshot kept
        self.anthias.down = False
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)

    def test_missing_alert_asset_changes_nothing(self):
        self.cfg.alert_assets = ["Weather Alerts", "Does Not Exist"]
        self.nws.add("Tornado Warning")
        with self.assertRaises(aw.AnthiasError) as ctx:
            self.daemon.tick(None)
        self.assertIn("Does Not Exist", str(ctx.exception))
        self.assertEqual(self.anthias.patches, [])
        self.assertEqual(self.controller.mode, "normal")

    def test_asset_added_during_alert_is_left_alone(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.anthias.assets["new"] = {"asset_id": "new", "name": "Added Later", "is_enabled": True,
                                      "is_active": True, "play_order": 99}
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertTrue(self.anthias.assets["new"]["is_enabled"])


class TestOverride(Base):
    def test_force_alert_and_back_to_auto(self):
        self.daemon.tick("alert")
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)
        self.daemon.tick(None)  # override removed, no NWS alerts: exits immediately
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)

    def test_force_normal_during_real_warning(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.daemon.tick("normal")
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)
        self.daemon.tick(None)  # back to auto: warning still active
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)

    def test_force_alert_then_real_warning_then_auto_keeps_alert(self):
        self.daemon.tick("alert")
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "alert")
        self.assertEqual(self.controller.state["alerts"][0]["event"], "Tornado Warning")

    def test_override_file_round_trip(self):
        self.store.write_override("alert")
        self.assertEqual(self.store.read_override(), "alert")
        self.store.write_override(None)
        self.assertIsNone(self.store.read_override())
        with open(self.store.override_path, "w") as fh:
            fh.write("banana")
        self.assertIsNone(self.store.read_override())


class TestAuth(Base):
    require_auth = True

    def test_works_with_credentials(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)

    def test_missing_credentials_gives_clear_error(self):
        client = aw.AnthiasClient(self.url, None, None, 5)
        with self.assertRaises(aw.AnthiasError) as ctx:
            client.list_assets()
        self.assertIn("authentication", str(ctx.exception))


class TestConfigValidation(unittest.TestCase):
    def load(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(text)
        try:
            return aw.load_config(fh.name)
        finally:
            os.unlink(fh.name)

    GOOD = '[alert_mode]\nassets=["A"]\n[nws]\npoint="39.7,-86.1"\nuser_agent="x (me@home)"\n'

    def test_good(self):
        cfg = self.load(self.GOOD)
        self.assertEqual(cfg.nws_events, aw.DEFAULT_EVENTS)
        self.assertEqual(cfg.anthias_url, "http://127.0.0.1")

    def test_placeholder_user_agent_rejected(self):
        with self.assertRaises(aw.ConfigError):
            self.load(self.GOOD.replace("me@home", "you@example.com"))

    def test_needs_location(self):
        with self.assertRaises(aw.ConfigError):
            self.load('[alert_mode]\nassets=["A"]\n[nws]\nuser_agent="x (me@home)"\n')

    def test_zones_only_ok(self):
        cfg = self.load('[alert_mode]\nassets=["A"]\n[nws]\nzones=["inc097"]\nuser_agent="x (me@home)"\n')
        self.assertEqual(cfg.nws_zones, ["INC097"])

    def test_half_credentials_rejected(self):
        with self.assertRaises(aw.ConfigError):
            self.load(self.GOOD + '[anthias]\nusername="admin"\n')

    def test_bad_point_rejected(self):
        with self.assertRaises(aw.ConfigError):
            self.load(self.GOOD.replace("39.7,-86.1", "Indianapolis"))


class TestCLI(Base):
    def test_check_list_status_force(self):
        import contextlib
        import io
        path = os.path.join(self.tmp.name, "config.toml")
        self.nws.add("Tornado Warning")
        self.nws.add("Wind Advisory")
        for argv, code in ((["check"], 0), (["list-assets"], 0), (["status"], 0),
                           (["force", "alert"], 0), (["status"], 0), (["force", "auto"], 0),
                           (["restore"], 0)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(aw.main(["-c", path] + argv), code, argv)
            if argv == ["check"]:
                self.assertIn("[TRIGGERS] Tornado Warning", out.getvalue())
                self.assertIn("[ignored ] Wind Advisory", out.getvalue())
                self.assertIn("All good.", out.getvalue())

    def test_restore_command_after_service_stopped(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        path = os.path.join(self.tmp.name, "config.toml")
        self.assertEqual(aw.main(["-c", path, "restore", "--yes"]), 0)
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)


if __name__ == "__main__":
    unittest.main()


class TestDuplicatesAndOrder(Base):
    """The real-world loop: two copies of the weather pages (start and middle
    of the loop), and assets created in a different order than they play."""

    def setUp(self):
        super().setUp()
        a = self.anthias
        a.assets.clear()
        # Created (database) order deliberately differs from play order.
        created = [
            ("v3", "Political Video 3"), ("wA2", "Weather Alerts"), ("v1", "Political Video 1"),
            ("wR1", "Weather Radar"), ("wC1", "Weather Current"), ("v2", "Political Video 2"),
            ("wR2", "Weather Radar"), ("wA1", "Weather Alerts"), ("wC2", "Weather Current"),
            ("v4", "Political Video 4"), ("off", "Old Ad"),
        ]
        play = ["wC1", "wA1", "wR1", "v1", "v2", "wC2", "wA2", "wR2", "v3", "v4"]
        for aid, name in created:
            a.add(aid, name, enabled=aid != "off", play_order=play.index(aid) if aid in play else 10)
        self.original = a.playing()
        self.original_ids = [x for x in sorted(a.assets, key=lambda k: a.assets[k]["play_order"])]

    def test_alert_mode_plays_each_alert_page_once(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.anthias.playing(), ["Weather Alerts", "Weather Radar"])
        # The first copy in the loop is used, and the screen jumps to it.
        self.assertTrue(self.anthias.assets["wA1"]["is_enabled"])
        self.assertFalse(self.anthias.assets["wA2"]["is_enabled"])
        self.assertEqual(self.anthias.controls[-1], "asset&wA1")

    def test_order_is_restored_exactly(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(self.anthias.playing(), self.original)
        live = [k for k in self.original_ids if self.anthias.assets[k]["is_enabled"]]
        restored = sorted(live, key=lambda k: self.anthias.assets[k]["play_order"])
        self.assertEqual(restored, [k for k in self.original_ids if k != "off"])

    def test_without_order_fix_anthias_would_scramble(self):
        """Proves the fake reproduces the bug: re-enabling alone scrambles."""
        a = self.anthias
        for aid in ["v1", "v2", "wC2", "wA2"]:
            a.assets[aid]["is_enabled"] = False
            a.finalize_update(aid)
        for aid in ["v1", "v2", "wC2", "wA2"]:
            a.assets[aid]["is_enabled"] = True
            a.finalize_update(aid)
        self.assertNotEqual(a.playing(), self.original)

    def test_order_restored_after_restart_mid_alert(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.rebuild()
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertEqual(self.anthias.playing(), self.original)

    def test_asset_added_during_alert_goes_to_end(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.anthias.add("new", "Added Later", enabled=True, play_order=0)
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertEqual(self.anthias.playing(), self.original + ["Added Later"])

    def test_specific_copy_by_asset_id(self):
        self.cfg.alert_assets = ["wA2", "Weather Radar"]
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertTrue(self.anthias.assets["wA2"]["is_enabled"])
        self.assertFalse(self.anthias.assets["wA1"]["is_enabled"])
        self.assertEqual(self.anthias.playing(), ["Weather Alerts", "Weather Radar"])

    def test_radar_listed_first_plays_first(self):
        self.cfg.alert_assets = ["Weather Radar", "Weather Alerts"]
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.assertEqual(self.anthias.playing(), ["Weather Radar", "Weather Alerts"])

    def test_someone_reenables_second_copy_during_alert(self):
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        self.anthias.assets["wA2"]["is_enabled"] = True
        self.rebuild()  # restart re-applies the alert set
        self.daemon.tick(None)
        self.assertEqual(self.anthias.playing(), ["Weather Alerts", "Weather Radar"])

    def test_state_saved_by_previous_version_still_restores(self):
        """A state.json from version 0.1.0 has no saved order; restore must not crash."""
        self.nws.add("Tornado Warning")
        self.daemon.tick(None)
        state = self.store.load()
        del state["order"]
        self.store.save(state)
        self.rebuild()
        self.nws.features.clear()
        self.daemon.tick(None)
        self.daemon.tick(None)
        self.assertEqual(self.controller.mode, "normal")
        self.assertEqual(sorted(self.anthias.playing()), sorted(self.original))

    def test_list_assets_marks_one_copy(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            aw.main(["-c", os.path.join(self.tmp.name, "config.toml"), "list-assets"])
        lines = out.getvalue().splitlines()
        alerts = [l for l in lines if "Weather Alerts" in l]
        self.assertEqual(len(alerts), 2)
        self.assertIn(" * ", alerts[0])
        self.assertIn("copy", alerts[1])
        self.assertTrue(lines[1].lstrip().startswith("1 "))


class TestTimedOverride(Base):
    def test_timed_alert_expires_back_to_normal(self):
        future = dt.datetime.now(UTC) + dt.timedelta(minutes=20)
        self.store.write_override("alert", future)
        self.assertEqual(self.store.read_override(), "alert")
        self.daemon.tick(self.store.read_override())
        self.assertEqual(self.anthias.enabled_names(), self.ALERT)
        self.store.write_override("alert", dt.datetime.now(UTC) - dt.timedelta(seconds=1))
        value, until = self.store.read_override_detail()
        self.assertIsNone(value)
        self.assertIsNotNone(until)
        self.daemon.tick(self.store.read_override())
        self.assertEqual(self.anthias.enabled_names(), self.NORMAL)

    def test_garbage_end_time_is_ignored(self):
        with open(self.store.override_path, "w") as fh:
            fh.write("alert tomorrow-ish\n")
        self.assertIsNone(self.store.read_override())

    def test_cli_minutes(self):
        import contextlib
        import io
        path = os.path.join(self.tmp.name, "config.toml")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            self.assertEqual(aw.main(["-c", path, "force", "alert", "--minutes", "20"]), 0)
            self.assertEqual(aw.main(["-c", path, "status"]), 0)
            self.assertEqual(aw.main(["-c", path, "force", "auto", "--minutes", "5"]), 2)
            self.assertEqual(aw.main(["-c", path, "force", "alert", "--minutes", "0"]), 2)
        self.assertIn("then back to automatic", out.getvalue())
        self.assertIn("then automatic", out.getvalue())
        self.assertEqual(self.store.read_override(), "alert")

    def test_check_hint_when_anthias_is_off(self):
        import contextlib
        import io
        self.cfg.anthias_url = "http://127.0.0.1:1"
        path = os.path.join(self.tmp.name, "config.toml")
        with open(path) as fh:
            text = fh.read().replace(self.url + '"\n', 'http://127.0.0.1:1"\n', 1)
        with open(path, "w") as fh:
            fh.write(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(aw.main(["-c", path, "check"]), 1)
        self.assertIn("Expected if Anthias is switched off", out.getvalue())
        self.assertIn("reachable, no active alerts", out.getvalue())
