"""Regression checks for redacted telemetry and blank dashboard passwords."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


APP = Path(__file__).resolve().parents[1] / "app"
tree = ast.parse((APP / "schemas.py").read_text())
nodes = [node for node in tree.body if
         isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SERVICE_PASSWORD_FIELDS" for t in node.targets)
         or isinstance(node, ast.FunctionDef) and node.name in ("preserve_service_passwords", "service_config_deployment_payload")]
scope = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "schemas.py", "exec"), scope)
preserve = scope["preserve_service_passwords"]


class ServicePasswordPreservationTests(unittest.TestCase):
    def test_blank_missing_and_null_keep_all_stored_passwords(self):
        stored = {key: "stored-" + key for key in scope["SERVICE_PASSWORD_FIELDS"]}
        for blank in ({}, dict.fromkeys(stored), dict.fromkeys(stored, "")):
            with self.subTest(blank=blank):
                self.assertEqual(preserve(stored, blank), stored)
        self.assertEqual(preserve({}, {"password1": None, "password2": ""}), {})

    def test_replacement_and_nonsecret_settings_are_applied(self):
        stored = {"password1": "old", "password2": "other", "mountpoint1": "old-mp"}
        incoming = {"password1": " new with spaces ", "mountpoint1": "TV869", "server1_stream_active": True}
        result = preserve(stored, incoming)
        self.assertEqual(result["password1"], " new with spaces ")
        self.assertEqual(result["password2"], "other")
        self.assertEqual(result["mountpoint1"], "TV869")
        self.assertTrue(result["server1_stream_active"])
        self.assertEqual(stored["password1"], "old")
        self.assertNotIn("password2", incoming)

    def test_actual_telemetry_update_keeps_passwords(self):
        source = (APP / "crud.py").read_text()
        block = source[source.index('    previous_service_config = _ensure_dict(existing_device.service_config)'):
                       source.index('    # Status\n', source.index('    previous_service_config = _ensure_dict(existing_device.service_config)'))]
        import textwrap
        stored = {"password1": "caster-secret", "password2": "backup-secret", "mountpoint1": "TV869", "serverhost1": "caster", "server1_enabled": True}
        context = {"existing_device": SimpleNamespace(service_config=stored, server_config_lock=None),
                   "new_service_config": {"mountpoint1": "TV869", "server1_stream_active": True},
                   "_ensure_dict": lambda value: value, "schemas": SimpleNamespace(preserve_service_passwords=preserve),
                   "serial": "LP_E051D80E3511", "is_out_of_sync": False,
                   "update_values": {}, "should_update": False}
        exec(textwrap.dedent(block), context)
        updated = context["update_values"]["service_config"]
        self.assertEqual(updated["password1"], "caster-secret")
        self.assertEqual(updated["password2"], "backup-secret")
        self.assertTrue(updated["server1_stream_active"])
        self.assertEqual(updated["serverhost1"], "caster")
        self.assertTrue(updated["server1_enabled"])

    def test_legacy_deployment_keeps_full_saved_config(self):
        saved = {"password1": "new-secret", "username1": "source", "serverhost1": "caster",
                 "port1": 2101, "mountpoint1": "BASE", "server1_enabled": True,
                 "stream_on_demand": True, "server1_stream_active": False}
        changes = {"password1": "new-secret"}
        deploy = scope["service_config_deployment_payload"]
        for version in ("V2.2.5", "V2.2.4", None, "unknown"):
            self.assertEqual(deploy(version, saved, changes), saved)
        for version in ("V2.2.6", "V2.2.7", "V3.0.0"):
            self.assertEqual(deploy(version, saved, changes), changes)
        # Actual damaged ATG.PHU report: only stream flags, no caster config.
        damaged = {"stream_active": True, "server1_stream_on_demand": False,
                   "server1_stream_active": True, "server2_stream_on_demand": False,
                   "server2_stream_active": True}
        self.assertEqual(deploy("V2.2.6", saved, changes, damaged), saved)
        self.assertEqual(deploy("V2.2.6", saved, {}, damaged), saved)
        healthy = {key: value for key, value in saved.items() if key not in scope["SERVICE_PASSWORD_FIELDS"]}
        self.assertEqual(deploy("V2.2.6", saved, changes, healthy), changes)

    def test_endpoint_saves_merged_config_but_sends_only_patch(self):
        tree = ast.parse((APP / "device_command_routes.py").read_text())
        endpoint = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "configure_service_endpoint")
        def assignment(name):
            return next(node for node in endpoint.body if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))
        incoming = assignment("incoming_config")
        blank_filter = next(node for node in endpoint.body if isinstance(node, ast.For))
        mode_assignments = [assignment(name) for name in (
            "old_on_demand", "new_on_demand", "old_s1_on_demand", "old_s2_on_demand",
            "new_s1_on_demand", "new_s2_on_demand", "station_mode_changed")]
        stored = {"password1": "caster-secret", "username1": "source", "stream_on_demand": True,
                  "server1_stream_on_demand": True, "server1_enabled": True}
        for patch in ({"mountpoint1": "TV869", "password1": ""},
                      {"mountpoint1": "TV869", "password1": "replacement"}):
            context = {"serial": "LP_TEST", "old_service_cfg": stored,
                       "payload": SimpleNamespace(model_dump=lambda **kw: dict(patch) if kw.get("exclude_unset") else self.fail("Defaults must not become edits")),
                       "schemas": SimpleNamespace(SERVICE_PASSWORD_FIELDS=scope["SERVICE_PASSWORD_FIELDS"],
                                                   service_config_deployment_payload=scope["service_config_deployment_payload"]),
                       "device_in_db": SimpleNamespace(version="V2.2.6"),
                       "mqtt_handler": SimpleNamespace(device_sync_cache={}),
                       "_preserve_relay_config_metadata": lambda serial, old, updated: updated}
            nodes = [incoming, blank_filter, *mode_assignments, assignment("service_config_payload"), assignment("pi_command")]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), context)
            self.assertFalse(context["station_mode_changed"])
            saved = context["service_config_payload"]
            sent = context["pi_command"]["payload"]
            self.assertEqual(saved["username1"], "source")
            self.assertTrue(saved["server1_enabled"])
            self.assertNotIn("username1", sent)
            self.assertNotIn("server1_enabled", sent)
            self.assertEqual(saved["password1"], patch["password1"] or "caster-secret")
            self.assertEqual(sent, {key: value for key, value in patch.items() if value != ""})


if __name__ == "__main__":
    unittest.main()
