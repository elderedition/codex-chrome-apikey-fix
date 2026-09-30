"""本地 Chrome 请求身份策略管理器；仅使用 Python 标准库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager


ROOT = Path(__file__).absolute().parent
STATE_SCHEMA = 3
POLICY_LIMIT = 4096
HASH = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){2}\Z")


class Refusal(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def refuse(code, message):
    raise Refusal(code, message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(path):
    return os.path.normcase(os.path.normpath(str(path)))


def same(left, right):
    return canonical(left) == canonical(right)


def under(path, directory):
    try:
        return os.path.commonpath([canonical(path), canonical(directory)]) == canonical(directory)
    except ValueError:
        return False


def absolute(value, label):
    if not isinstance(value, str) or not value or "\x00" in value:
        refuse("unsafe-path", f"{label} must be an absolute local path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//")):
        refuse("unsafe-path", f"{label} must be an absolute local path without traversal")
    if os.name == "nt":
        for component in path.parts[1:]:
            if ":" in component or component.endswith((" ", ".")) or component.split(".")[0].upper() in {
                "CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]
            }:
                refuse("unsafe-path", f"{label} contains a reserved path component")
    return path


def inspect_path(path, kind=None, required=False):
    # 不能先 resolve()，否则链接或 junction 的证据会丢失。
    chain = list(reversed(path.parents)) + [path]
    for item in chain:
        try:
            info = os.lstat(item)
        except FileNotFoundError:
            if item == path and required:
                refuse("missing-target", "A required file or directory is missing")
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            refuse("unsafe-path", "Links and reparse points are not permitted")
        expected = kind if item == path else "directory"
        if expected == "directory" and not stat.S_ISDIR(info.st_mode):
            refuse("unsafe-path", "A directory path is not a regular directory")
        if expected == "file" and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            refuse("unsafe-path", "A file path is not a regular unaliased file")


def read_bytes(path, limit=64 * 1024 * 1024):
    inspect_path(path, "file", True)
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        refuse("invalid-input", "Input exceeds its allowed size")
    return value


def decode(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        refuse("invalid-input", "Input is not valid UTF-8")


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            refuse("invalid-input", "Duplicate JSON fields are not accepted")
        result[key] = value
    return result


def parse_json(data):
    try:
        return json.loads(decode(data), object_pairs_hook=json_object,
                          parse_constant=lambda unused: refuse("invalid-input", "Nonfinite JSON is invalid"))
    except (ValueError, TypeError):
        refuse("invalid-input", "Input is not valid JSON")


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, indent=2) + "\n").encode("utf-8")


def atomic_write(path, data, expected=None, new_only=False):
    inspect_path(path.parent, "directory", True)
    inspect_path(path, "file")
    descriptor, scratch = tempfile.mkstemp(prefix=".ccp-", suffix=".tmp", dir=path.parent)
    scratch = Path(scratch)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        inspect_path(path, "file")
        if new_only and path.exists():
            refuse("conflict", "Destination already exists")
        if expected is not None and (not path.exists() or digest(read_bytes(path)) != expected):
            refuse("conflict", "Target changed before replacement; no replacement was made")
        os.replace(scratch, path)
    finally:
        if scratch.exists():
            scratch.unlink()


def toml_string(source):
    source = source.lstrip()
    if not source or source[0] not in "\"'" or source.startswith(('"""', "'''")):
        refuse("config-invalid", "Expected a supported one-line TOML string")
    quote, output, index = source[0], [], 1
    escapes = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
    while index < len(source):
        char = source[index]
        index += 1
        if char == quote:
            tail = source[index:].strip()
            if tail and not tail.startswith("#"):
                refuse("config-invalid", "Ambiguous TOML string suffix")
            return "".join(output)
        if ord(char) < 32 and char != "\t":
            refuse("config-invalid", "Control characters are invalid in this TOML string")
        if char == "\\" and quote == '"':
            if index == len(source):
                refuse("config-invalid", "Incomplete TOML escape")
            escape = source[index]
            index += 1
            if escape in escapes:
                output.append(escapes[escape])
                continue
            if escape in ("u", "U"):
                size = 4 if escape == "u" else 8
                sequence = source[index:index + size]
                if len(sequence) != size or not re.fullmatch(r"[0-9a-fA-F]+", sequence):
                    refuse("config-invalid", "Invalid TOML Unicode escape")
                number = int(sequence, 16)
                if number > 0x10FFFF or 0xD800 <= number <= 0xDFFF:
                    refuse("config-invalid", "Invalid TOML Unicode scalar")
                output.append(chr(number))
                index += size
                continue
            refuse("config-invalid", "Unsupported TOML escape")
        output.append(char)
    refuse("config-invalid", "Unterminated TOML string")


def configuration(home):
    target = "mcp_servers.node_repl.env"
    found, active, values = False, False, {}
    text = decode(read_bytes(home / "config.toml", 4 * 1024 * 1024))
    if '"""' in text or "'''" in text:
        refuse("config-invalid", "Multiline TOML strings are outside the supported configuration subset")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            match = re.fullmatch(r"(\[\[?)(.*?)(\]\]?)\s*(?:#.*)?", line)
            if not match:
                refuse("config-invalid", "Unsupported or ambiguous TOML table header")
            opening, contents, closing = match.groups()
            if len(opening) != len(closing):
                refuse("config-invalid", "Invalid TOML table delimiter")
            pieces, offset = [], 0
            while offset < len(contents):
                component = re.match(r'''\s*("(?:[^"\\]|\\.)*"|'[^']*'|[A-Za-z0-9_-]+)\s*''', contents[offset:])
                if not component:
                    refuse("config-invalid", "Unsupported TOML table key")
                raw_key = component.group(1)
                pieces.append(toml_string(raw_key) if raw_key[0] in "\"'" else raw_key)
                offset += component.end()
                if offset < len(contents):
                    if contents[offset] != "." or offset + 1 == len(contents):
                        refuse("config-invalid", "Invalid TOML dotted table key")
                    offset += 1
            active = pieces == target.split(".")
            if active and len(opening) != 1:
                refuse("config-invalid", "The node_repl environment cannot be an array table")
            if active and found:
                refuse("config-invalid", "Duplicate node_repl environment table")
            found = found or active
            continue
        if not active:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\s*=\s*(.*)", line)
        if not match:
            refuse("config-invalid", "Unsupported node_repl environment assignment")
        key, value = match.groups()
        if key in ("NODE_REPL_TRUSTED_SERVICES", "NODE_REPL_NODE_PATH"):
            if key in values:
                refuse("config-invalid", "Duplicate required configuration key")
            values[key] = toml_string(value)
    if set(values) != {"NODE_REPL_TRUSTED_SERVICES", "NODE_REPL_NODE_PATH"}:
        refuse("config-invalid", "Required node_repl environment keys are missing")
    services = parse_json(values["NODE_REPL_TRUSTED_SERVICES"].encode("utf-8"))
    if not isinstance(services, dict) or not isinstance(services.get("browser"), str):
        refuse("config-invalid", "Trusted services must contain a browser path")
    return absolute(services["browser"], "browser service"), absolute(values["NODE_REPL_NODE_PATH"], "node executable")


def load_compatibility():
    value = parse_json(read_bytes(ROOT / "compatibility.json", 65536))
    required = {"schema", "implementation", "marker", "factory", "controlToken", "versions"}
    if not isinstance(value, dict) or set(value) != required or type(value["schema"]) is not int or value["schema"] != 1:
        refuse("implementation-invalid", "Invalid compatibility manifest")
    if any(not isinstance(value[key], str) or not value[key] for key in required - {"schema", "versions"}):
        refuse("implementation-invalid", "Invalid compatibility identifiers")
    if not isinstance(value["versions"], dict) or not value["versions"]:
        refuse("implementation-invalid", "Invalid supported versions")
    for version, entry in value["versions"].items():
        if not VERSION.fullmatch(version) or not isinstance(entry, dict) or set(entry) != {"sha256", "binding", "replacement"}:
            refuse("implementation-invalid", "Invalid compatibility entry")
        if not isinstance(entry["sha256"], str) or not HASH.fullmatch(entry["sha256"]) or any(
            not isinstance(entry[key], str) or not entry[key] for key in ("binding", "replacement")
        ):
            refuse("implementation-invalid", "Invalid compatibility values")
    return value


class Manager:
    def __init__(self, args):
        home_value = args.codex_home if args.codex_home is not None else os.environ.get("CODEX_HOME")
        state_value = args.state_dir if args.state_dir is not None else os.environ.get("CHROME_COMPAT_STATE_DIR")
        self.home = absolute(home_value if home_value is not None else str(Path.home() / ".codex"), "configuration home")
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        self.directory = absolute(state_value if state_value is not None else str(Path(local) / "ChromeCompatPatch"), "state directory")
        self.runtime = self.home / "plugins" / "cache"
        if under(self.directory, self.runtime) or same(self.directory, self.home):
            refuse("unsafe-path", "State directory must be separate from runtime and configuration roots")
        inspect_path(self.home, "directory", True)
        inspect_path(self.directory, "directory")
        self.target, self.node = configuration(self.home)
        browser_root = self.runtime / "openai-bundled" / "browser"
        try:
            relative = self.target.relative_to(browser_root)
        except ValueError:
            refuse("unsafe-path", "Browser path does not use the verified cache layout")
        if len(relative.parts) != 3 or relative.parts[1:] != ("scripts", "browser-service.mjs") or not VERSION.fullmatch(relative.parts[0]):
            refuse("unsafe-path", "Browser path does not use the verified cache layout")
        self.version = relative.parts[0]
        inspect_path(self.target, "file")
        inspect_path(self.node, "file", True)
        if os.name == "nt" and self.node.suffix.lower() != ".exe":
            refuse("config-invalid", "Node path must name an executable")
        if same(self.node, self.target) or under(self.directory, self.target.parent):
            refuse("unsafe-path", "Runtime and state paths must not alias")
        self.control = self.directory / "control.json"
        self.state_path = self.directory / "state.json"
        self.backups = self.directory / "backups"
        self.compat = load_compatibility()
        self.helper = read_bytes(ROOT / "identification-helper.mjs", 65536)
        self.helper_hash = digest(self.helper)
        self.state = self.read_state()

    def empty_state(self):
        return {"schema": STATE_SCHEMA, "codexHome": str(self.home), "stateDir": str(self.directory), "entries": []}

    def read_state(self):
        inspect_path(self.state_path, "file")
        if not self.state_path.exists():
            return self.empty_state()
        value = parse_json(read_bytes(self.state_path, 1024 * 1024))
        if not isinstance(value, dict) or type(value.get("schema")) is not int:
            refuse("state-invalid", "State must be an object with an integer schema")
        schema = value["schema"]
        if schema == 1:
            refuse("migration-required", "Repository-local schema 1 has no trustworthy configuration binding")
        expected = {"schema", "codexHome", "entries"} | ({"stateDir"} if schema == STATE_SCHEMA else set())
        if schema not in (2, STATE_SCHEMA) or set(value) != expected:
            refuse("state-invalid", "Unsupported state schema or unexpected fields")
        stored_home = absolute(value["codexHome"], "stored configuration home")
        if not same(stored_home, self.home):
            refuse("foreign-state", "State belongs to a different configuration home")
        if schema == STATE_SCHEMA and not same(absolute(value["stateDir"], "stored state directory"), self.directory):
            refuse("control-path-mismatch", "State directory moved; embedded control paths have not moved")
        if not isinstance(value["entries"], list):
            refuse("state-invalid", "State entries must be an array")
        seen = set()
        for entry in value["entries"]:
            fields = {"version", "before", "after"} | ({"controlPath", "implementation", "helperSha256", "phase"} if schema == STATE_SCHEMA else set())
            if not isinstance(entry, dict) or set(entry) != fields:
                refuse("state-invalid", "Unexpected installation record fields")
            if not isinstance(entry["version"], str) or not VERSION.fullmatch(entry["version"]) or entry["version"] in seen:
                refuse("state-invalid", "Invalid or duplicate installation version")
            seen.add(entry["version"])
            if any(not isinstance(entry[key], str) or not HASH.fullmatch(entry[key]) for key in ("before", "after")) or entry["before"] == entry["after"]:
                refuse("state-invalid", "Invalid installation digests")
            reviewed = self.compat["versions"].get(entry["version"])
            if reviewed is None and schema == 2:
                refuse("migration-required", "Unreviewed schema 2 records cannot be verified by this implementation")
            if reviewed is not None and entry["before"] != reviewed["sha256"]:
                refuse("state-invalid", "Recorded original digest is not approved for this version")
            if schema == STATE_SCHEMA:
                if not same(absolute(entry["controlPath"], "stored control path"), self.control):
                    refuse("control-path-mismatch", "Recorded control path differs from the selected state directory")
                if entry["phase"] not in ("prepared", "installed") or not isinstance(entry["implementation"], str) or not entry["implementation"]:
                    refuse("state-invalid", "Invalid installation metadata")
                if not isinstance(entry["helperSha256"], str) or not HASH.fullmatch(entry["helperSha256"]):
                    refuse("state-invalid", "Invalid helper digest")
        return value

    def save_state(self):
        atomic_write(self.state_path, encoded(self.state))

    def target_for(self, version):
        return self.runtime / "openai-bundled" / "browser" / version / "scripts" / "browser-service.mjs"

    def record(self):
        return next((entry for entry in self.state["entries"] if entry["version"] == self.version), None)

    def backup(self, entry):
        path = self.backups / (entry["before"] + ".mjs")
        if not path.exists():
            refuse("backup-invalid", "Required original backup is missing")
        data = read_bytes(path)
        if digest(data) != entry["before"]:
            refuse("backup-invalid", "Original backup digest does not match")
        return data

    def control_enabled(self):
        try:
            policy = parse_json(read_bytes(self.control, POLICY_LIMIT))
            return isinstance(policy, dict) and set(policy) == {"schema", "enabled", "requireIdentification"} and \
                type(policy["schema"]) is int and policy["schema"] == 1 and \
                type(policy["enabled"]) is bool and policy["enabled"] and \
                type(policy["requireIdentification"]) is bool and policy["requireIdentification"]
        except (Refusal, OSError):
            return False

    def set_control(self, enabled):
        atomic_write(self.control, encoded({"schema": 1, "enabled": enabled, "requireIdentification": True}))

    def candidate(self, original, version=None):
        version = version or self.version
        reviewed = self.compat["versions"].get(version)
        if reviewed is not None and digest(original) != reviewed["sha256"]:
            refuse("conflict", "Service does not have the approved pristine digest")
        source = decode(original)
        if self.compat["marker"] in source or self.compat["factory"] in source:
            refuse("conflict", "An unexpected identification patch is already present")
        # 未验证版本只尝试已有调用规则；版本和摘要未知不代表接口必然不兼容。
        # 多版本可能共用规则，先去重；缺失、多处匹配或互相冲突的替换均不猜测。
        entries = [reviewed] if reviewed is not None else self.compat["versions"].values()
        matches = {(item["binding"], item["replacement"]) for item in entries if item["binding"] in source}
        if len(matches) != 1:
            refuse("incompatible-structure", "No unique known patch rule matches this service")
        binding, replacement = next(iter(matches))
        if source.count(binding) != 1:
            refuse("incompatible-structure", "The patch insertion point is not unique")
        helper = decode(self.helper)
        if helper.count(self.compat["controlToken"]) != 1:
            refuse("implementation-invalid", "Helper control placeholder is not unique")
        helper = helper.replace(self.compat["controlToken"], json.dumps(str(self.control), ensure_ascii=True))
        return (source.replace(binding, replacement, 1) + "\n" + self.compat["marker"] + "\n" + helper).encode("utf-8")

    def validate_node(self, candidate):
        inspect_path(self.node, "file", True)
        try:
            checked = subprocess.run([str(self.node), "--check", "--input-type=module"], input=candidate,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            refuse("node-check-failed", "Configured Node could not complete syntax validation")
        if checked.returncode:
            refuse("node-check-failed", "Configured Node rejected candidate module syntax")

    def verify_current(self, entry, actual):
        original = self.backup(entry)
        if self.state["schema"] != STATE_SCHEMA:
            refuse("migration-required", "Schema 2 supports verified restoration but is not a current installation")
        if entry["implementation"] != self.compat["implementation"] or entry["helperSha256"] != self.helper_hash:
            refuse("migration-required", "Installation was produced by a different implementation")
        expected = self.candidate(original, entry["version"])
        if digest(expected) != entry["after"] or expected != actual:
            refuse("control-path-mismatch", "Installed bytes do not match the current helper and embedded control path")

    def report(self, status, **extra):
        success = status in ("original", "patched", "disabled", "preview")
        reviewed = self.version in self.compat["versions"]
        return {"ok": success, **({"code": status} if not success else {}), "status": status, "version": self.version, "target": str(self.target),
                "stateDir": str(self.directory), "runtimeLoaded": "not-checked",
                "versionValidation": "reviewed" if reviewed else "unverified",
                "warnings": [] if reviewed else ["This browser plugin version has not been validated. Existing patch rules may be tried, but correct operation is not guaranteed."],
                **extra}

    def status(self):
        if not self.target.exists():
            return self.report("missing-target")
        actual = read_bytes(self.target)
        current = digest(actual)
        entry = self.record()
        if entry:
            self.backup(entry)
            if current == entry["before"]:
                return self.report("recovery-required" if entry.get("phase") == "prepared" else "original", enabled=False)
            if current != entry["after"]:
                refuse("conflict", "Recorded service was changed; it will not be overwritten")
            if self.state["schema"] == 2:
                return self.report("migration-required", enabled=False)
            self.verify_current(entry, actual)
            if entry["phase"] != "installed":
                return self.report("recovery-required", enabled=False)
            enabled = self.control_enabled()
            return self.report("patched" if enabled else "disabled", enabled=enabled)
        self.candidate(actual)
        return self.report("original", enabled=False)

    @contextmanager
    def mutation(self):
        inspect_path(self.directory, "directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        inspect_path(self.directory, "directory", True)
        lock = self.directory / "mutation.lock"
        inspect_path(lock, "file")
        try:
            descriptor = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            refuse("locked", "Mutation lock exists; after confirming no process is active, recover the lock manually")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded({"pid": os.getpid(), "schema": 1}))
                stream.flush()
                os.fsync(stream.fileno())
            # 等待锁的间隙内，另一个调用可能已更新状态。
            self.state = self.read_state()
            yield
        finally:
            inspect_path(lock, "file", True)
            lock.unlink()

    def install(self):
        with self.mutation():
            if self.state["schema"] != STATE_SCHEMA:
                refuse("migration-required", "Restore the schema 2 installation before installing this implementation")
            if not self.target.exists():
                refuse("missing-target", "Browser service is missing")
            original = read_bytes(self.target)
            entry = self.record()
            if entry and digest(original) == entry["after"]:
                self.verify_current(entry, original)
                if entry["phase"] == "prepared":
                    entry["phase"] = "installed"
                    self.save_state()
                if not self.control_enabled():
                    refuse("disabled", "Installation is present but disabled; use enable explicitly")
                return self.status()
            if entry:
                self.backup(entry)
                if digest(original) != entry["before"] or entry["phase"] != "prepared":
                    refuse("conflict", "Recorded installation no longer matches its installed bytes")
            candidate = self.candidate(original)
            self.validate_node(candidate)
            inspect_path(self.backups, "directory")
            self.backups.mkdir(exist_ok=True)
            before, after = digest(original), digest(candidate)
            backup_path = self.backups / (before + ".mjs")
            if backup_path.exists():
                if digest(read_bytes(backup_path)) != before:
                    refuse("backup-invalid", "Existing backup is damaged; installation refused")
            else:
                atomic_write(backup_path, original, new_only=True)
            if entry is None:
                entry = {"version": self.version, "before": before, "after": after, "controlPath": str(self.control),
                         "implementation": self.compat["implementation"], "helperSha256": self.helper_hash, "phase": "prepared"}
                self.state["entries"].append(entry)
                # 先保持关闭并保存恢复信息，最后才允许策略生效。
                self.set_control(False)
                self.save_state()
                fresh = True
            else:
                if entry["after"] != after or entry["helperSha256"] != self.helper_hash or entry["implementation"] != self.compat["implementation"]:
                    refuse("migration-required", "Prepared installation belongs to a different implementation")
                fresh = False
            atomic_write(self.target, candidate, expected=before)
            entry["phase"] = "installed"
            self.save_state()
            if fresh:
                self.set_control(True)
            return self.status()

    def disable(self):
        with self.mutation():
            self.set_control(False)
            return self.report("disabled", enabled=False)

    def enable(self):
        with self.mutation():
            if not self.state["entries"] or self.state["schema"] != STATE_SCHEMA:
                refuse("migration-required", "Enable requires a complete current installation")
            for entry in self.state["entries"]:
                if entry["phase"] != "installed":
                    refuse("recovery-required", "An installation transaction is incomplete")
                actual = read_bytes(self.target_for(entry["version"]))
                if digest(actual) != entry["after"]:
                    refuse("conflict", "An installed service was modified or restored")
                self.verify_current(entry, actual)
            if self.record() is None:
                refuse("conflict", "The configured browser service has no current installation")
            self.set_control(True)
            return self.status()

    def uninstall(self):
        with self.mutation():
            # 先停用本地策略；任意备份或目标冲突都保留原文件。
            self.set_control(False)
            plans = []
            for entry in self.state["entries"]:
                original = self.backup(entry)
                target = self.target_for(entry["version"])
                current = digest(read_bytes(target))
                if current not in (entry["before"], entry["after"]):
                    refuse("conflict", "An installed service changed; uninstall left it intact")
                if entry["version"] not in self.compat["versions"] and current == entry["after"]:
                    # 未验证版本没有预先认可的原件摘要，恢复前核对备份到安装件的完整变换。
                    self.verify_current(entry, read_bytes(target))
                plans.append((entry, target, original, current))
            for entry, target, original, current in plans:
                if current == entry["after"]:
                    atomic_write(target, original, expected=entry["after"])
                self.state["entries"].remove(entry)
                self.save_state()
            self.state = self.empty_state()
            self.save_state()
            return self.report("original", enabled=False, backupsRetained=True)

    def preview(self, output):
        if output is None:
            refuse("output-required", "Preview requires --output with a new absolute file path")
        path = absolute(output, "preview output")
        if under(path, self.home / "plugins") or under(path, self.directory) or any(same(path, other) for other in (
            self.target, self.node, self.home / "config.toml", ROOT / "patch.py", ROOT / "identification-helper.mjs", ROOT / "compatibility.json"
        )):
            refuse("unsafe-path", "Preview output must be separate from runtime, state, and tool files")
        inspect_path(path.parent, "directory", True)
        inspect_path(path, "file")
        if path.exists():
            refuse("conflict", "Preview output already exists")
        candidate = self.candidate(read_bytes(self.target))
        self.validate_node(candidate)
        # 显式新建：preview 不创建状态目录或恢复文件。
        with path.open("xb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        return self.report("preview", output=str(path), sha256=digest(candidate))


class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        refuse("arguments-invalid", "Invalid command line arguments; use --help for usage")


def main(argv=None):
    try:
        parser = JsonParser(description="Local Chrome request-identification compatibility utility")
        parser.add_argument("command", nargs="?", default="status", choices=("status", "preview", "install", "disable", "enable", "uninstall"))
        parser.add_argument("--codex-home")
        parser.add_argument("--state-dir")
        parser.add_argument("--output")
        args = parser.parse_args(argv)
        if args.output is not None and args.command != "preview":
            refuse("arguments-invalid", "--output is valid only for preview")
        manager = Manager(args)
        result = manager.preview(args.output) if args.command == "preview" else getattr(manager, args.command)()
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["ok"] else 1
    except Refusal as error:
        print(json.dumps({"ok": False, "code": error.code, "status": error.code, "message": error.message,
                          "runtimeLoaded": "not-checked"}, ensure_ascii=True))
        return 1
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        print(json.dumps({"ok": False, "code": "io-or-input-error", "status": "conflict",
                          "message": "Local input or filesystem operation failed; inspect paths and retry conservatively",
                          "runtimeLoaded": "not-checked"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
