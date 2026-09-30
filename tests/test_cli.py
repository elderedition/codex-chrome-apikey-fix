import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).absolute().parents[1]
NODE = os.environ.get("CHROME_COMPAT_TEST_NODE") or shutil.which("node.exe") or shutil.which("node")
VERSION = "26.924.51851"
BINDING = "new th(r,this.clientApi,()=>qe(this.runtime),this.turnEndedTracker,a_)"
SOURCE = ("const a_=function(){return false}; const qe=r=>r; class th {}\n"
          "class SyntheticHost { start(r) { return " + BINDING + "; } }\n").encode("utf-8")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")


@unittest.skipUnless(NODE, "Node is required; set CHROME_COMPAT_TEST_NODE or add Node to PATH")
class CliTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="cli-", dir=ROOT / "temp")
        self.root = Path(self.temporary.name)
        self.tool = self.root / "tool with spaces"
        self.tool.mkdir()
        for name in ("patch.py", "patch.cmd", "launch.ps1", "identification-helper.mjs", "compatibility.json"):
            shutil.copyfile(ROOT / name, self.tool / name)
        self.home = self.root / "config home"
        self.state = self.root / "stable state"
        self.target = self.home / "plugins" / "cache" / "openai-bundled" / "browser" / VERSION / "scripts" / "browser-service.mjs"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(SOURCE)
        self.configure()
        compatibility = json.loads((self.tool / "compatibility.json").read_text(encoding="utf-8"))
        compatibility["versions"][VERSION]["sha256"] = hashlib.sha256(SOURCE).hexdigest()
        write_json(self.tool / "compatibility.json", compatibility)
        self.env = os.environ.copy()
        for key in ("CODEX_HOME", "CHROME_COMPAT_STATE_DIR", "CHROME_COMPAT_PYTHON"):
            self.env.pop(key, None)
        self.env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    def tearDown(self):
        self.temporary.cleanup()

    def configure(self, service=None, node=None, basic=False):
        services = json.dumps({"browser": str(service or self.target)})
        quote = json.dumps if basic else lambda text: "'" + text + "'"
        text = ("secret = 'do-not-print-this-secret'\n[mcp_servers.node_repl.env]\n"
                "NODE_REPL_TRUSTED_SERVICES = " + quote(services) + " # public path only\n"
                "NODE_REPL_NODE_PATH = " + quote(str(node or NODE)) + "\n")
        (self.home / "config.toml").write_text(text, encoding="utf-8")

    def run_cli(self, command=None, *, ok=True, extra=(), env=None, launcher=False):
        if launcher:
            argv = ["powershell.exe", "-NoLogo", "-NoProfile", "-File", str(self.tool / "launch.ps1")]
        else:
            argv = [sys.executable, str(self.tool / "patch.py")]
        if command:
            argv.append(command)
        argv += ["--codex-home", str(self.home), "--state-dir", str(self.state), *extra]
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env or self.env, timeout=45)
        try:
            payload = json.loads(result.stdout.strip() or result.stderr.strip())
        except Exception:
            self.fail(f"CLI did not produce JSON: returncode={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}")
        self.assertNotIn("do-not-print-this-secret", result.stdout + result.stderr)
        self.assertEqual(result.returncode == 0, ok, payload)
        self.assertEqual(payload["runtimeLoaded"], "not-checked")
        return payload

    def read_state(self):
        return json.loads((self.state / "state.json").read_text(encoding="utf-8"))

    def backup(self):
        return self.state / "backups" / (hashlib.sha256(SOURCE).hexdigest() + ".mjs")

    def use_unverified_version(self, source=SOURCE):
        self.target = self.home / "plugins" / "cache" / "openai-bundled" / "browser" / "99.1.2" / "scripts" / "browser-service.mjs"
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(source)
        self.configure()

    def test_default_status_is_read_only(self):
        result = self.run_cli()
        self.assertEqual(result["status"], "original")
        self.assertEqual(result["versionValidation"], "reviewed")
        self.assertEqual(result["warnings"], [])
        self.assertFalse(self.state.exists())
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_lifecycle_preserves_disabled_and_original_bytes(self):
        self.assertEqual(self.run_cli("install")["status"], "patched")
        installed = self.target.read_bytes()
        self.assertNotEqual(installed, SOURCE)
        self.assertEqual(self.backup().read_bytes(), SOURCE)
        self.assertEqual(self.run_cli("install")["status"], "patched")
        self.run_cli("disable")
        self.assertEqual(self.run_cli()["status"], "disabled")
        self.assertEqual(self.run_cli("install", ok=False)["status"], "disabled")
        self.run_cli("enable")
        self.assertEqual(self.target.read_bytes(), installed)
        self.run_cli("uninstall")
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertEqual(self.backup().read_bytes(), SOURCE)
        self.assertEqual(self.run_cli()["status"], "original")

    def test_preview_is_explicit_new_file_and_leaves_state_absent(self):
        preview = self.root / "preview file.mjs"
        self.run_cli("preview", extra=("--output", str(preview)))
        self.assertTrue(preview.is_file())
        self.assertIn(b"ChromeCompatPatch", preview.read_bytes())
        self.assertFalse(self.state.exists())
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.run_cli("preview", ok=False, extra=("--output", str(preview)))
        self.run_cli("preview", ok=False)
        self.run_cli("preview", ok=False, extra=("--output", str(self.target.parent / "candidate.mjs")))
        self.run_cli("preview", ok=False, extra=("--output", str(self.state / "state.json")))

    def test_modified_source_and_invalid_syntax_refuse(self):
        self.target.write_bytes(SOURCE + b"// updater change\n")
        self.run_cli("install", ok=False)
        self.assertEqual(self.target.read_bytes(), SOURCE + b"// updater change\n")
        invalid = SOURCE + b"syntax!invalid!\n"
        self.target.write_bytes(invalid)
        manifest = json.loads((self.tool / "compatibility.json").read_text(encoding="utf-8"))
        manifest["versions"][VERSION]["sha256"] = hashlib.sha256(invalid).hexdigest()
        write_json(self.tool / "compatibility.json", manifest)
        self.assertEqual(self.run_cli("install", ok=False)["code"], "node-check-failed")
        self.assertEqual(self.target.read_bytes(), invalid)

    def test_nonunique_binding_refuses_with_approved_synthetic_hash(self):
        duplicate = SOURCE + SOURCE
        self.target.write_bytes(duplicate)
        manifest = json.loads((self.tool / "compatibility.json").read_text(encoding="utf-8"))
        manifest["versions"][VERSION]["sha256"] = hashlib.sha256(duplicate).hexdigest()
        write_json(self.tool / "compatibility.json", manifest)
        self.run_cli("install", ok=False)
        self.assertEqual(self.target.read_bytes(), duplicate)

    def test_control_exact_types_and_malformed_input_are_disabled(self):
        self.run_cli("install")
        invalid = [None, [], True, 1, {"schema": True, "enabled": True, "requireIdentification": True},
                   {"schema": 1, "enabled": 1, "requireIdentification": True},
                   {"schema": 1, "enabled": True, "requireIdentification": "true"},
                   {"schema": 1, "enabled": True, "requireIdentification": True, "extra": 0}]
        for policy in invalid:
            with self.subTest(policy=policy):
                write_json(self.state / "control.json", policy)
                self.assertEqual(self.run_cli()["status"], "disabled")
        (self.state / "control.json").write_text("{" + " " * 5000, encoding="utf-8")
        self.assertEqual(self.run_cli()["status"], "disabled")

    def test_damaged_backup_prevents_enable_restore_and_idempotence(self):
        self.run_cli("install")
        installed = self.target.read_bytes()
        self.backup().write_bytes(b"damaged")
        for command in ("install", "enable", "uninstall"):
            with self.subTest(command=command):
                self.run_cli(command, ok=False)
                self.assertEqual(self.target.read_bytes(), installed)
        self.assertFalse(json.loads((self.state / "control.json").read_text(encoding="utf-8"))["enabled"])

    def test_modified_installed_target_survives_uninstall(self):
        self.run_cli("install")
        modified = self.target.read_bytes() + b"// external modification"
        self.target.write_bytes(modified)
        self.run_cli("uninstall", ok=False)
        self.assertEqual(self.target.read_bytes(), modified)
        self.assertTrue(self.backup().exists())

    def test_foreign_state_and_structural_corruption(self):
        self.run_cli("install")
        valid = self.read_state()
        corrupt = [[], None, True, {**valid, "schema": True}, {**valid, "entries": {}},
                   {**valid, "entries": [valid["entries"][0], valid["entries"][0]]},
                   {**valid, "codexHome": str(self.root / "foreign")}, {**valid, "surprise": 1},
                   {**valid, "entries": [{**valid["entries"][0], "before": "../../bad"}]}]
        installed = self.target.read_bytes()
        for state in corrupt:
            with self.subTest(state=state):
                write_json(self.state / "state.json", state)
                self.run_cli("uninstall", ok=False)
                self.assertEqual(self.target.read_bytes(), installed)

    def test_duplicate_json_keys_are_rejected(self):
        self.run_cli("install")
        (self.state / "state.json").write_text('{"schema":2,"schema":3}', encoding="utf-8")
        self.run_cli(ok=False)

    def test_unapproved_original_in_otherwise_self_consistent_state_is_rejected(self):
        self.run_cli("install")
        installed = self.target.read_bytes()
        state = self.read_state()
        unapproved = b"unapproved original"
        forged_digest = hashlib.sha256(unapproved).hexdigest()
        state["entries"][0]["before"] = forged_digest
        (self.state / "backups" / (forged_digest + ".mjs")).write_bytes(unapproved)
        write_json(self.state / "state.json", state)
        self.assertEqual(self.run_cli("uninstall", ok=False)["code"], "state-invalid")
        self.assertEqual(self.target.read_bytes(), installed)

    def test_current_bytes_and_embedded_control_must_match_regenerated_candidate(self):
        self.run_cli("install")
        state = self.read_state()
        changed = self.target.read_bytes().replace(b"control.json", b"foreign.json")
        self.target.write_bytes(changed)
        state["entries"][0]["after"] = hashlib.sha256(changed).hexdigest()
        write_json(self.state / "state.json", state)
        self.assertEqual(self.run_cli("enable", ok=False)["code"], "control-path-mismatch")
        self.assertEqual(self.target.read_bytes(), changed)

    def test_repository_relocation_and_state_relocation(self):
        self.run_cli("install")
        relocated = self.root / "relocated tools"
        self.tool.rename(relocated)
        self.tool = relocated
        self.assertEqual(self.run_cli()["status"], "patched")
        old_state = self.state
        self.state = self.root / "moved state"
        old_state.rename(self.state)
        self.assertEqual(self.run_cli(ok=False)["code"], "control-path-mismatch")
        self.run_cli("uninstall", ok=False)

    def test_schema2_migration_and_verified_restoration(self):
        self.run_cli("install")
        legacy = self.read_state()
        legacy = {"schema": 2, "codexHome": str(self.home), "entries": [
            {key: entry[key] for key in ("version", "before", "after")} for entry in legacy["entries"]]}
        write_json(self.state / "state.json", legacy)
        self.assertEqual(self.run_cli(ok=False)["status"], "migration-required")
        self.assertEqual(self.run_cli("install", ok=False)["code"], "migration-required")
        self.run_cli("uninstall")
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertEqual(self.read_state()["schema"], 3)
        self.assertTrue(self.backup().exists())

    def test_schema1_refuses_even_restoration(self):
        self.state.mkdir()
        write_json(self.state / "state.json", {"schema": 1, "entries": []})
        self.assertEqual(self.run_cli("uninstall", ok=False)["code"], "migration-required")
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_prepared_transaction_can_complete_without_auto_enable(self):
        self.run_cli("install")
        state = self.read_state()
        state["entries"][0]["phase"] = "prepared"
        write_json(self.state / "state.json", state)
        self.target.write_bytes(SOURCE)
        self.run_cli("disable")
        self.assertEqual(self.run_cli(ok=False)["status"], "recovery-required")
        self.assertEqual(self.run_cli("install")["status"], "disabled")
        self.assertEqual(self.read_state()["entries"][0]["phase"], "installed")

    def test_prepared_after_replace_can_recover_or_restore(self):
        self.run_cli("install")
        state = self.read_state()
        state["entries"][0]["phase"] = "prepared"
        write_json(self.state / "state.json", state)
        self.assertEqual(self.run_cli(ok=False)["status"], "recovery-required")
        self.run_cli("enable", ok=False)
        self.run_cli("uninstall")
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_stale_lock_gives_controlled_refusal(self):
        self.state.mkdir()
        (self.state / "mutation.lock").write_text("stale", encoding="utf-8")
        self.assertEqual(self.run_cli("install", ok=False)["code"], "locked")
        self.assertEqual(self.target.read_bytes(), SOURCE)
        self.assertEqual(self.run_cli()["status"], "original")

    def test_config_strings_layout_and_missing_target(self):
        self.configure(basic=True)
        self.assertEqual(self.run_cli()["status"], "original")
        self.configure(service=self.root / "other.mjs")
        self.assertEqual(self.run_cli(ok=False)["code"], "unsafe-path")
        missing = self.target.parent / "browser-service.mjs"
        self.configure(service=missing)
        self.target.unlink()
        self.assertEqual(self.run_cli(ok=False)["status"], "missing-target")

    def test_unverified_lifecycle_preview_and_warning_with_different_digest(self):
        source = SOURCE + b"// synthetic newer build\n"
        self.use_unverified_version(source)
        status = self.run_cli()
        self.assertEqual(status["status"], "original")
        self.assertEqual(status["versionValidation"], "unverified")
        self.assertTrue(status["warnings"])
        preview = self.root / "unverified-preview.mjs"
        self.run_cli("preview", extra=("--output", str(preview)))
        self.assertFalse(self.state.exists())
        self.assertEqual(self.target.read_bytes(), source)
        result = self.run_cli("install")
        self.assertEqual(result["status"], "patched")
        self.assertEqual(result["versionValidation"], "unverified")
        self.assertTrue(result["warnings"])
        self.assertEqual(self.target.read_bytes(), preview.read_bytes())
        self.assertEqual(self.run_cli("install")["status"], "patched")
        self.run_cli("disable")
        self.assertEqual(self.run_cli()["status"], "disabled")
        self.run_cli("install", ok=False)
        self.assertEqual(self.run_cli("enable")["status"], "patched")
        self.run_cli("uninstall")
        self.assertEqual(self.target.read_bytes(), source)
        backup = self.state / "backups" / (hashlib.sha256(source).hexdigest() + ".mjs")
        self.assertEqual(backup.read_bytes(), source)
        self.assertEqual(self.run_cli()["status"], "original")

    def test_unverified_missing_duplicate_patch_and_invalid_syntax_refuse(self):
        self.use_unverified_version()
        cases = [
            (SOURCE.replace(BINDING.encode(), b"null"), "incompatible-structure"),
            (SOURCE + SOURCE, "incompatible-structure"),
            (SOURCE + b"\n/* ChromeCompatPatch:20260930:v1 */", "conflict"),
            (SOURCE + b"\nsyntax!invalid!", "node-check-failed"),
        ]
        for index, (source, code) in enumerate(cases):
            with self.subTest(code=code, index=index):
                self.target.write_bytes(source)
                preview = self.root / (str(index) + ".mjs")
                self.assertEqual(self.run_cli("preview", ok=False, extra=("--output", str(preview)))["code"], code)
                self.assertFalse(preview.exists())
                self.assertEqual(self.run_cli("install", ok=False)["code"], code)
                self.assertEqual(self.target.read_bytes(), source)
                self.assertFalse((self.state / "state.json").exists())
                self.assertFalse((self.state / "backups").exists())

    def test_unverified_shared_rules_are_deduplicated_but_ambiguity_refuses(self):
        self.use_unverified_version()
        manifest_path = self.tool / "compatibility.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        original_rule = manifest["versions"][VERSION]
        manifest["versions"]["98.1.2"] = dict(original_rule)
        write_json(manifest_path, manifest)
        self.assertEqual(self.run_cli()["status"], "original")
        alternate = BINDING.replace("a_)", "otherPolicy)")
        cases = [
            (SOURCE, {**original_rule, "replacement": "null"}),
            (SOURCE + (alternate + ";\n").encode(), {**original_rule, "binding": alternate}),
        ]
        for source, rule in cases:
            with self.subTest(rule=rule):
                manifest["versions"]["98.1.2"] = rule
                write_json(manifest_path, manifest)
                self.target.write_bytes(source)
                self.assertEqual(self.run_cli("install", ok=False)["code"], "incompatible-structure")
                self.assertEqual(self.target.read_bytes(), source)

    def test_unverified_modified_installation_and_damaged_backup_are_preserved(self):
        self.use_unverified_version()
        self.run_cli("install")
        installed = self.target.read_bytes()
        self.backup().write_bytes(b"damaged")
        for command in ("status", "install", "enable", "uninstall"):
            self.assertEqual(self.run_cli(command, ok=False)["code"], "backup-invalid")
            self.assertEqual(self.target.read_bytes(), installed)
        self.backup().write_bytes(SOURCE)
        modified = installed + b"// external modification\n"
        self.target.write_bytes(modified)
        self.assertEqual(self.run_cli("uninstall", ok=False)["code"], "conflict")
        self.assertEqual(self.target.read_bytes(), modified)
        self.assertEqual(self.backup().read_bytes(), SOURCE)

    def test_unverified_forged_backup_must_reproduce_installed_bytes(self):
        self.use_unverified_version()
        self.run_cli("install")
        installed = self.target.read_bytes()
        state = self.read_state()
        # 即便伪造备份仍含合法绑定，也必须能重建当前安装件才能恢复。
        forged = SOURCE + b"// not the installed original\n"
        forged_digest = hashlib.sha256(forged).hexdigest()
        state["entries"][0]["before"] = forged_digest
        (self.state / "backups" / (forged_digest + ".mjs")).write_bytes(forged)
        write_json(self.state / "state.json", state)
        self.assertEqual(self.run_cli("uninstall", ok=False)["code"], "control-path-mismatch")
        self.assertEqual(self.target.read_bytes(), installed)

    def test_unverified_prepared_installation_can_recover(self):
        self.use_unverified_version()
        self.run_cli("install")
        state = self.read_state()
        state["entries"][0]["phase"] = "prepared"
        write_json(self.state / "state.json", state)
        self.target.write_bytes(SOURCE)
        self.run_cli("disable")
        self.assertEqual(self.run_cli(ok=False)["status"], "recovery-required")
        self.assertEqual(self.run_cli("install")["status"], "disabled")
        self.assertEqual(self.read_state()["entries"][0]["phase"], "installed")
        self.run_cli("uninstall")
        self.assertEqual(self.target.read_bytes(), SOURCE)

    def test_unverified_legacy_state_still_requires_migration(self):
        self.use_unverified_version()
        self.run_cli("install")
        installed = self.target.read_bytes()
        entry = self.read_state()["entries"][0]
        write_json(self.state / "state.json", {"schema": 2, "codexHome": str(self.home), "entries": [
            {key: entry[key] for key in ("version", "before", "after")}]})
        self.assertEqual(self.run_cli("uninstall", ok=False)["code"], "migration-required")
        self.assertEqual(self.target.read_bytes(), installed)

    def test_invalid_config_types_duplicate_and_multiline_strings(self):
        examples = [
            "NODE_REPL_TRUSTED_SERVICES = true",
            "NODE_REPL_TRUSTED_SERVICES = '[]'",
            "NODE_REPL_TRUSTED_SERVICES = '{\"browser\":1}'",
            "NODE_REPL_TRUSTED_SERVICES = '''multiline'''",
        ]
        for example in examples:
            with self.subTest(example=example):
                (self.home / "config.toml").write_text("[mcp_servers.node_repl.env]\n" + example +
                    "\nNODE_REPL_NODE_PATH = '" + str(NODE) + "'\n", encoding="utf-8")
                self.run_cli("install", ok=False)
                self.assertEqual(self.target.read_bytes(), SOURCE)
        self.configure()
        text = (self.home / "config.toml").read_text(encoding="utf-8")
        (self.home / "config.toml").write_text(text + "NODE_REPL_NODE_PATH = 'duplicate'\n", encoding="utf-8")
        self.run_cli(ok=False)

    def test_unrelated_quoted_and_array_toml_tables(self):
        self.configure()
        path = self.home / "config.toml"
        text = path.read_text(encoding="utf-8")
        unrelated = ('[plugins."synthetic@bundle"]\nenabled = true\n'
                     "[projects.'X:\\Synthetic Folder']\ntrust = 'local'\n"
                     "[[unrelated.items]]\nname = 'example'\n")
        path.write_text(unrelated + text, encoding="utf-8")
        self.assertEqual(self.run_cli()["status"], "original")
        path.write_text(text + unrelated, encoding="utf-8")
        self.assertEqual(self.run_cli()["status"], "original")
        path.write_text(text.replace("[mcp_servers.node_repl.env]", '[mcp_servers."node_repl".env]'), encoding="utf-8")
        self.assertEqual(self.run_cli()["status"], "original")

    def test_environment_overrides_and_explicit_precedence(self):
        env = {**self.env, "CODEX_HOME": str(self.root / "unused"), "CHROME_COMPAT_STATE_DIR": str(self.root / "unused state")}
        self.assertEqual(self.run_cli(env=env)["status"], "original")
        env.update(CODEX_HOME=str(self.home), CHROME_COMPAT_STATE_DIR=str(self.state))
        completed = subprocess.run([sys.executable, str(self.tool / "patch.py")], env=env,
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["stateDir"], str(self.state))

    def test_last_moment_updater_change_is_not_overwritten(self):
        driver = self.root / "synthetic_race.py"
        driver.write_text(
            "import pathlib, sys\nsys.path.insert(0, " + repr(str(self.tool)) + ")\nimport patch\n"
            "original_run = patch.subprocess.run\n"
            "def race(*args, **kwargs):\n"
            "    result = original_run(*args, **kwargs)\n"
            "    pathlib.Path(" + repr(str(self.target)) + ").write_bytes(b'updater output')\n"
            "    return result\n"
            "patch.subprocess.run = race\n"
            "sys.exit(patch.main(['install', '--codex-home', " + repr(str(self.home)) +
            ", '--state-dir', " + repr(str(self.state)) + "]))\n", encoding="utf-8")
        completed = subprocess.run([sys.executable, str(driver)], env=self.env, capture_output=True,
                                   text=True, encoding="utf-8", timeout=30)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["code"], "conflict")
        self.assertEqual(self.target.read_bytes(), b"updater output")
        self.assertEqual(self.backup().read_bytes(), SOURCE)
        self.assertEqual(self.read_state()["entries"][0]["phase"], "prepared")

    def test_relative_overrides_and_state_in_runtime_are_rejected(self):
        self.run_cli(ok=False, extra=("--codex-home", "."))
        self.run_cli(ok=False, extra=("--state-dir", str(self.target.parent / "state")))
        self.run_cli(ok=False, extra=("--state-dir", str(self.home)))

    def test_symbolic_link_target_and_parent_are_rejected(self):
        real = self.root / "real.mjs"
        real.write_bytes(SOURCE)
        self.target.unlink()
        try:
            self.target.symlink_to(real)
        except OSError as error:
            self.skipTest("Symbolic links are unavailable: " + str(error.winerror if hasattr(error, "winerror") else error.errno))
        self.assertEqual(self.run_cli("install", ok=False)["code"], "unsafe-path")
        self.assertEqual(real.read_bytes(), SOURCE)

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_launcher_override_spaces_and_child_exit(self):
        env = {**self.env, "CHROME_COMPAT_PYTHON": sys.executable}
        self.assertEqual(self.run_cli(launcher=True, env=env)["status"], "original")
        self.run_cli("not-a-command", launcher=True, env=env, ok=False)
        invalid = {**env, "CHROME_COMPAT_PYTHON": str(self.root / "missing python.exe")}
        self.assertEqual(self.run_cli(launcher=True, env=invalid, ok=False)["code"], "python-override-invalid")


if __name__ == "__main__":
    unittest.main()
