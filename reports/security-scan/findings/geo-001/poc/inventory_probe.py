#!/usr/bin/env python3
"""Offline inventory of literal MQTT password/default patterns.

The probe deliberately masks all credential material and performs no network
operations. It can read a checkout directly or use local ``git show`` for an
exact revision.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


TARGETS = (
    "geodetic/agent_universal.py",
    "geodetic/geotek_daithanh_aitogy.py",
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    value: str


def read_source(repo: Path, relative_path: str, revision: str | None) -> str:
    if revision:
        object_name = f"{revision}:{relative_path}"
        if any(char in object_name for char in "\r\n\0"):
            raise RuntimeError("invalid Git object name")
        completed = subprocess.run(
            ["git", "cat-file", "--batch=%(objecttype)"],
            cwd=repo,
            input=(object_name + "\n").encode("utf-8"),
            check=False,
            capture_output=True,
        )
        kind, _, content = completed.stdout.partition(b"\n")
        if completed.returncode != 0 or kind != b"blob" or not content.endswith(b"\n"):
            raise RuntimeError(f"cannot read {relative_path} at the requested revision")
        return content[:-1].decode("utf-8", errors="replace")

    path = repo / relative_path
    return path.read_text(encoding="utf-8")


def assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def literal_mqtt_password(value: ast.AST) -> str | None:
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value

    if not isinstance(value, ast.Call) or not value.args:
        return None

    function_name = value.func.id if isinstance(value.func, ast.Name) else ""
    if function_name != "config_value" or len(value.args) < 4:
        return None

    section = value.args[0]
    default = value.args[-1]
    if not (
        isinstance(section, ast.Constant)
        and section.value == "mqtt"
        and isinstance(default, ast.Constant)
        and isinstance(default.value, str)
    ):
        return None
    return default.value


def mqtt_dict_passwords(tree: ast.AST, relative_path: str) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "mqtt"):
                continue
            if not isinstance(value, ast.Dict):
                continue
            for nested_key, nested_value in zip(value.keys, value.values):
                if not (
                    isinstance(nested_key, ast.Constant)
                    and str(nested_key.value).lower() in {"password", "pass", "pwd"}
                    and isinstance(nested_value, ast.Constant)
                    and isinstance(nested_value.value, str)
                ):
                    continue
                findings.append(
                    Finding(relative_path, nested_value.lineno, nested_value.value)
                )
    return findings


def scan_source(source: str, relative_path: str) -> list[Finding]:
    tree = ast.parse(source, filename=relative_path)
    findings = mqtt_dict_passwords(tree, relative_path)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = assigned_names(node)
        if not any("MQTT" in name.upper() and "PASS" in name.upper() for name in names):
            continue
        value_node = node.value
        if value_node is None:
            continue
        literal = literal_mqtt_password(value_node)
        if literal is not None:
            findings.append(Finding(relative_path, node.lineno, literal))

    # A single AST shape can be recognized by more than one rule. Collapse only
    # exact path/line/value duplicates; independently embedded defaults remain.
    return sorted(set(findings), key=lambda item: (item.path, item.line))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline, masked inventory of hard-coded MQTT passwords"
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="source checkout")
    parser.add_argument("--revision", help="optional local Git revision to inspect")
    args = parser.parse_args()

    repo = args.repo.resolve()
    findings: list[Finding] = []
    try:
        for relative_path in TARGETS:
            source = read_source(repo, relative_path, args.revision)
            findings.extend(scan_source(source, relative_path))
    except (OSError, RuntimeError, SyntaxError) as exc:
        print(f"[-] inventory failed: {exc}", file=sys.stderr)
        return 1

    if not findings:
        print("[+] no literal MQTT password/default found in the reviewed agent sources")
        return 0

    for finding in findings:
        print(
            f"[!] hard-coded MQTT password/default: "
            f"{finding.path}:{finding.line} value=<MASKED>"
        )

    value_groups: dict[str, list[Finding]] = {}
    for finding in findings:
        value_groups.setdefault(finding.value, []).append(finding)
    repeated = [group for group in value_groups.values() if len({x.path for x in group}) > 1]
    for group in repeated:
        print(
            f"[!] one MQTT password literal repeats {len(group)} times "
            f"across {len({x.path for x in group})} files"
        )

    print(f"[!] vulnerable credential pattern present ({len(findings)} occurrences)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

