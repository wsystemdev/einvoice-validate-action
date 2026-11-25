#!/usr/bin/env python3
"""Validate EU e-invoices (UBL, CII, XRechnung, ZUGFeRD/Factur-X PDF) offline in CI
with the W System validator (or optionally via the W System API).

Outputs per-file GitHub annotations, a job summary Markdown table, JSON and SARIF 2.1.0
reports, GitHub Action outputs, and an exit code. Standard library only.
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

UA = "wsystem-einvoice-validate-action/2.0"
DEFAULT_IMAGE = "ghcr.io/wsystemdev/einvoice-validator:0.1"
EXTS = (".xml", ".pdf")
DISCLAIMER = (
    "Technical check against the official EN 16931 / XRechnung rule sets. "
    "A technically valid file is not tax or legal advice. "
    "Peppol BIS 3 specific rules are not checked."
)
VALID_RULES = ("auto", "en16931", "xrechnung")
VALID_FAIL_ON = ("error", "warning", "none")
VALID_ENGINES = ("local", "api")
VALID_REPORT_FORMATS = ("both", "sarif", "json", "none")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def esc(s: object, prop: bool = False) -> str:
    text = str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        text = text.replace(":", "%3A").replace(",", "%2C")
    return text


def md_escape(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def redact(text: str, *secrets: str) -> str:
    out = str(text)
    for sec in secrets:
        if sec:
            out = out.replace(sec, "***")
    return out


def collect(patterns: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for line in patterns.replace(",", "\n").splitlines():
        p = line.strip()
        if not p:
            continue
        if os.path.isdir(p):
            hits = [
                str(f)
                for f in sorted(pathlib.Path(p).rglob("*"))
                if f.suffix.lower() in EXTS and f.is_file()
            ]
        elif any(c in p for c in "*?["):
            hits = sorted(h for h in glob.glob(p, recursive=True) if os.path.isfile(h))
        else:
            hits = [p]
        for f in hits:
            norm = os.path.normpath(f)
            if norm not in seen:
                seen.add(norm)
                files.append(norm)
    return files


def to_workspace_rel(paths: list[str], workspace: str, allow_outside: bool = False) -> list[str]:
    ws = pathlib.Path(workspace).resolve()
    out: list[str] = []
    for p in paths:
        rp = pathlib.Path(p).resolve()
        try:
            rel = rp.relative_to(ws).as_posix()
            out.append(rel or ".")
        except ValueError:
            if allow_outside:
                out.append(pathlib.Path(p).as_posix())
            else:
                raise ValueError(
                    f"File '{p}' is outside the workspace '{ws}' and cannot be mounted into the offline container."
                )
    return out


def _issue_text(issue: dict) -> str:
    bts = issue.get("business_terms") or []
    msg = f"{issue.get('rule_id', '?')}: {str(issue.get('message', '')).strip()}"
    if bts:
        msg += f" [{', '.join(str(b) for b in bts)}]"
    if issue.get("fix"):
        msg += f" Fix: {issue['fix']}"
    return msg


def _normalize_result(r: dict, display_file: str) -> dict:
    item = dict(r)
    item["file"] = display_file
    item.setdefault("errors", [])
    item.setdefault("warnings", [])
    item["error_count"] = int(item.get("error_count", len(item["errors"])))
    item["warning_count"] = int(item.get("warning_count", len(item["warnings"])))
    item["valid"] = bool(item.get("valid", False))
    return item


def run_local(
    rel_files: list[str],
    workspace: str,
    rules: str,
    image: str,
    license_key: str,
    rule_pack: str,
    runner=subprocess.run,
) -> dict:
    wsv_bin = env("WSV_BIN")
    all_files: list[dict] = []
    meta_validator: dict = {"name": "W System e-invoice validator", "edition": "free", "offline": True}
    meta_pack = rule_pack or ""
    meta_disclaimer = DISCLAIMER

    batch_size = 200
    batches = [rel_files[i : i + batch_size] for i in range(0, len(rel_files), batch_size)]

    for batch in batches:
        env_file_path: str | None = None
        try:
            if wsv_bin:
                cmd = shlex.split(wsv_bin) + [
                    "validate",
                    "--rules",
                    rules,
                    "--format",
                    "json",
                ]
                if rule_pack:
                    cmd += ["--pack", rule_pack]
                cmd += ["--"] + batch
                proc_env = os.environ.copy()
                if license_key:
                    proc_env["WSV_LICENSE_KEY"] = license_key
                proc = runner(cmd, cwd=workspace, env=proc_env, capture_output=True, text=True)
            else:
                cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "-v",
                    f"{pathlib.Path(workspace).resolve()}:/work:ro",
                    "-w",
                    "/work",
                ]
                if license_key:
                    fd, env_file_path = tempfile.mkstemp(prefix="wsv-env-", text=True)
                    os.chmod(env_file_path, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(f"WSV_LICENSE_KEY={license_key}\n")
                    cmd += ["--env-file", env_file_path]
                cmd += [image, "validate", "--rules", rules, "--format", "json"]
                if rule_pack:
                    cmd += ["--pack", rule_pack]
                cmd += ["--"] + batch
                proc = runner(cmd, cwd=workspace, capture_output=True, text=True)
        finally:
            if env_file_path and os.path.exists(env_file_path):
                try:
                    os.unlink(env_file_path)
                except OSError:
                    pass

        if proc.returncode not in (0, 1):
            err_msg = redact((proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip(), license_key)
            raise RuntimeError(f"Offline validator failed (exit {proc.returncode}): {err_msg}")

        try:
            payload = json.loads(proc.stdout)
        except ValueError as e:
            err_msg = redact((proc.stderr or proc.stdout or str(e)).strip(), license_key)
            raise RuntimeError(f"Offline validator returned invalid JSON: {err_msg}") from e

        if isinstance(payload.get("validator"), dict):
            meta_validator = dict(payload["validator"])
            meta_validator.setdefault("offline", True)
        if payload.get("rule_pack"):
            meta_pack = str(payload["rule_pack"])
        if payload.get("disclaimer"):
            meta_disclaimer = str(payload["disclaimer"])

        raw_files = payload.get("files") or []
        for idx, item in enumerate(raw_files):
            fallback_name = batch[idx] if idx < len(batch) else "invoice"
            fname = item.get("file") or fallback_name
            all_files.append(_normalize_result(item, fname))

    return {
        "validator": meta_validator,
        "rule_pack": meta_pack,
        "disclaimer": meta_disclaimer,
        "files": all_files,
    }


def validate_api(
    path: str,
    api: str,
    rules: str,
    key: str,
    timeout: int = 60,
    opener=urllib.request.urlopen,
) -> tuple[int, dict]:
    with open(path, "rb") as fh:
        body = fh.read()
    ctype = "application/pdf" if body[:5] == b"%PDF-" else "application/xml"
    url = f"{api.rstrip('/')}/einvoice/validate?rules={rules}"
    headers = {"Content-Type": ctype, "User-Agent": UA, "Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    last = (0, {"detail": "request failed"})
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with opener(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
            except ValueError:
                payload = {}
            if e.code in (502, 503, 504) and attempt < 2:
                last = (e.code, payload)
                time.sleep(2 * (attempt + 1))
                continue
            return e.code, payload
        except (urllib.error.URLError, TimeoutError) as e:
            last = (0, {"detail": str(e)})
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return last


def run_api(
    files: list[str],
    api_url: str,
    rules: str,
    api_key: str,
    opener=urllib.request.urlopen,
) -> dict:
    results: list[dict] = []
    for f in files:
        try:
            status, d = validate_api(f, api_url, rules, api_key, opener=opener)
        except OSError as e:
            results.append(
                _normalize_result(
                    {"valid": False, "read_error": f"cannot read file: {e.strerror or str(e)}"},
                    f,
                )
            )
            continue
        if status == 200 and isinstance(d, dict) and "valid" in d:
            results.append(_normalize_result(d, f))
        else:
            detail = (d or {}).get("detail") or (d or {}).get("error") or ""
            hint = ""
            if status in (402, 429):
                hint = (
                    " Free calls for this runner IP are used up: add a free API key "
                    "(https://api.wsystem.dev/account/free-key) as 'api-key', or use engine: local (offline)."
                )
            results.append(
                _normalize_result(
                    {
                        "valid": False,
                        "input_error": f"HTTP {status} {detail}".strip() + hint,
                        "_http_status": status,
                    },
                    f,
                )
            )
    return {
        "validator": {"name": "W System e-invoice validation API", "edition": "api", "offline": False},
        "rule_pack": "hosted",
        "disclaimer": DISCLAIMER,
        "files": results,
    }


def evaluate_status(r: dict, fail_on: str) -> tuple[str, bool]:
    """Return (display_status, counts_as_failed)."""
    if r.get("input_error") or r.get("read_error"):
        return "not checked", True
    if not r["valid"]:
        return "invalid", True
    if fail_on == "warning" and r.get("warning_count"):
        return "warnings", True
    return "valid", False


def emit_annotations(results: list[dict], out) -> None:
    for r in results:
        f = r["file"]
        err_reason = r.get("input_error") or r.get("read_error")
        if err_reason:
            title = "validation call failed" if r.get("_http_status") else "not an e-invoice"
            print(f"::error file={esc(f, True)},title={esc(title, True)}::{esc(err_reason)}", file=out)
            continue
        for kind, lst in (("error", r.get("errors") or []), ("warning", r.get("warnings") or [])):
            for issue in lst:
                loc = f"file={esc(f, True)}"
                line = issue.get("line")
                if isinstance(line, int) and line >= 1:
                    loc += f",line={line}"
                title = esc(issue.get("rule_id") or kind, True)
                print(f"::{kind} {loc},title={title}::{esc(_issue_text(issue))}", file=out)


def build_summary_md(
    results: list[dict],
    failed: int,
    rule_pack: str,
    engine_mode: str,
    fail_on: str,
) -> str:
    icon = {
        "valid": "✅ valid",
        "invalid": "❌ invalid",
        "warnings": "⚠️ warnings",
        "not checked": "❌ not checked",
    }
    mode_label = "offline" if engine_mode == "local" else "API"
    pack_note = f" Rule pack `{md_escape(rule_pack)}`." if rule_pack else ""
    fail_note = " (`fail-on: none`)" if fail_on == "none" else ""
    rows = [
        f"## E-invoice validation (W System, {mode_label})",
        "",
        f"{len(results)} file(s) checked, **{failed} failed**{fail_note}.{pack_note}",
        "",
        "| File | Result | Profile | Syntax | Errors | Warnings | First issue |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        first = (r.get("errors") or r.get("warnings") or [{}])[0]
        first_issue = (
            r.get("input_error")
            or r.get("read_error")
            or (f"{first.get('rule_id')}: {str(first.get('message', ''))[:120]}" if first else "")
        )
        rows.append(
            f"| `{md_escape(r['file'])}` | {icon.get(r['status'], r['status'])} | "
            f"{md_escape(r.get('profile') or '')} | {md_escape(str(r.get('syntax') or '').upper())} | "
            f"{r.get('error_count', 0)} | {r.get('warning_count', 0)} | {md_escape(first_issue)} |"
        )
    rows += ["", f"_{DISCLAIMER}_", ""]
    return "\n".join(rows)


def build_sarif(results: list[dict], validator_meta: dict, rule_pack: str) -> dict:
    rules_map: dict[str, dict] = {}
    sarif_results: list[dict] = []
    for r in results:
        uri = pathlib.Path(r["file"]).as_posix()
        err_reason = r.get("input_error") or r.get("read_error")
        if err_reason:
            rid = "READ_ERROR" if r.get("read_error") else "INPUT_ERROR"
            rules_map.setdefault(
                rid,
                {
                    "id": rid,
                    "shortDescription": {"text": "E-invoice input or read failure"},
                    "properties": {"ruleset": "input", "rule_pack": rule_pack},
                },
            )
            sarif_results.append(
                {
                    "ruleId": rid,
                    "level": "error",
                    "message": {"text": str(err_reason)},
                    "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri}}}],
                }
            )
            continue
        for level, lst in (("error", r.get("errors") or []), ("warning", r.get("warnings") or [])):
            for issue in lst:
                rid = str(issue.get("rule_id") or f"WSYSTEM_{level.upper()}")
                rules_map.setdefault(
                    rid,
                    {
                        "id": rid,
                        "shortDescription": {"text": (str(issue.get("message") or rid).strip() or rid)[:200]},
                        "properties": {
                            "ruleset": str(issue.get("ruleset") or ""),
                            "rule_pack": rule_pack,
                        },
                    },
                )
                phys: dict = {"artifactLocation": {"uri": uri}}
                line = issue.get("line")
                if isinstance(line, int) and line >= 1:
                    phys["region"] = {"startLine": line}
                sarif_results.append(
                    {
                        "ruleId": rid,
                        "level": level,
                        "message": {"text": _issue_text(issue)},
                        "locations": [{"physicalLocation": phys}],
                    }
                )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": str(validator_meta.get("name") or "W System e-invoice validator"),
                        "version": str(validator_meta.get("version") or "0.1.0"),
                        "informationUri": "https://wsystem.dev",
                        "rules": list(rules_map.values()),
                    }
                },
                "results": sarif_results,
            }
        ],
    }


def _write_text(path_str: str, content: str) -> str:
    p = pathlib.Path(path_str)
    if p.parent and str(p.parent) != ".":
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def _write_outputs(
    gh_out: str,
    checked: int,
    failed: int,
    valid_flag: bool,
    json_path: str,
    sarif_path: str,
    rule_pack: str,
) -> None:
    if not gh_out:
        return
    with open(gh_out, "a", encoding="utf-8") as fh:
        fh.write(f"checked={checked}\n")
        fh.write(f"failed={failed}\n")
        fh.write(f"valid={'true' if valid_flag else 'false'}\n")
        fh.write(f"json-report={json_path}\n")
        fh.write(f"sarif-report={sarif_path}\n")
        fh.write(f"rule-pack={rule_pack}\n")


def run(
    argv=None,
    opener=urllib.request.urlopen,
    runner=subprocess.run,
    out=sys.stdout,
) -> tuple[int, list[dict]]:
    patterns = env("INPUT_FILES") or env("INPUT_PATHS") or "**/*.xml"
    rules = env("INPUT_RULES") or env("INPUT_PROFILE") or "auto"
    if env("INPUT_RULES", "") in ("", "auto") and env("INPUT_PROFILE"):
        rules = env("INPUT_PROFILE")
    fail_on = (env("INPUT_FAIL_ON") or "error").lower()
    if env("INPUT_FAIL_ON_WARNINGS", "false").lower() == "true" and fail_on == "error":
        fail_on = "warning"

    engine = (env("INPUT_ENGINE") or "local").lower()
    image = env("INPUT_IMAGE") or DEFAULT_IMAGE
    license_key = env("INPUT_LICENSE_KEY") or env("INPUT_LICENCE_KEY")
    rule_pack = env("INPUT_RULE_PACK")
    report_format = (env("INPUT_REPORT_FORMAT") or "both").lower()
    json_report_path = env("INPUT_JSON_REPORT", "wsystem-einvoice-report.json")
    sarif_report_path = env("INPUT_SARIF_REPORT", "wsystem-einvoice-report.sarif")
    fail_on_empty = env("INPUT_FAIL_ON_EMPTY", "false").lower() == "true"
    api_url = env("INPUT_API_URL", "https://api.wsystem.dev")
    api_key = env("INPUT_API_KEY")
    workspace = env("GITHUB_WORKSPACE") or os.getcwd()

    for label, val, allowed in (
        ("rules", rules, VALID_RULES),
        ("fail-on", fail_on, VALID_FAIL_ON),
        ("engine", engine, VALID_ENGINES),
        ("report-format", report_format, VALID_REPORT_FORMATS),
    ):
        if val not in allowed:
            print(f"::error title=invalid input::Invalid '{label}' value '{val}'; expected one of {', '.join(allowed)}.", file=out)
            return 2, []

    if license_key and engine == "local" and not env("WSV_BIN") and "einvoice-validator-pro" not in image:
        print(
            "::notice::license-key is set; set 'image' to the commercial validator image "
            "(for example ghcr.io/wsystemdev/einvoice-validator-pro:0.1) to enable commercial rule packs.",
            file=out,
        )

    files = collect(patterns)
    if not files:
        msg = "No files matched the 'files'/'paths' input; nothing validated."
        if fail_on_empty:
            print(f"::error title=no files matched::{msg}", file=out)
            _write_outputs(env("GITHUB_OUTPUT"), 0, 0, False, "", "", "")
            return 2, []
        print(f"::warning::{msg}", file=out)
        written_json = ""
        written_sarif = ""
        empty_meta = {"name": "W System e-invoice validator", "version": "0.1.0"}
        if report_format in ("both", "json") and json_report_path:
            written_json = _write_text(
                json_report_path,
                json.dumps(
                    {
                        "validator": {**empty_meta, "offline": engine == "local"},
                        "rule_pack": "",
                        "checked": 0,
                        "failed": 0,
                        "fail_on": fail_on,
                        "disclaimer": DISCLAIMER,
                        "files": [],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
        if report_format in ("both", "sarif") and sarif_report_path:
            written_sarif = _write_text(
                sarif_report_path,
                json.dumps(build_sarif([], empty_meta, ""), indent=2, ensure_ascii=False),
            )
        _write_outputs(env("GITHUB_OUTPUT"), 0, 0, True, written_json, written_sarif, "")
        return 0, []

    if engine == "local":
        try:
            allow_outside = bool(env("WSV_BIN"))
            rel_files = to_workspace_rel(files, workspace, allow_outside=allow_outside)
            raw = run_local(
                rel_files,
                workspace=workspace,
                rules=rules,
                image=image,
                license_key=license_key,
                rule_pack=rule_pack,
                runner=runner,
            )
        except (ValueError, RuntimeError) as e:
            print(f"::error title=validator failed::{esc(redact(str(e), license_key, api_key))}", file=out)
            return 1, []
    else:
        raw = run_api(files, api_url=api_url, rules=rules, api_key=api_key, opener=opener)

    results: list[dict] = []
    failed = 0
    for r in raw.get("files") or []:
        status, is_bad = evaluate_status(r, fail_on)
        item = dict(r)
        item["status"] = status
        if is_bad:
            failed += 1
        results.append(item)

    emit_annotations(results, out)

    rule_pack_used = str(raw.get("rule_pack") or "")
    validator_meta = raw.get("validator") or {"name": "W System e-invoice validator", "version": "0.1.0"}

    summary_path = env("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(build_summary_md(results, failed, rule_pack_used, engine, fail_on) + "\n")

    written_json = ""
    if report_format in ("both", "json") and json_report_path:
        json_payload = {
            "validator": validator_meta,
            "rule_pack": rule_pack_used,
            "checked": len(results),
            "failed": failed,
            "fail_on": fail_on,
            "offline": engine == "local",
            "disclaimer": raw.get("disclaimer") or DISCLAIMER,
            "files": results,
        }
        written_json = _write_text(json_report_path, json.dumps(json_payload, indent=2, ensure_ascii=False))

    written_sarif = ""
    if report_format in ("both", "sarif") and sarif_report_path:
        sarif_payload = build_sarif(results, validator_meta, rule_pack_used)
        written_sarif = _write_text(sarif_report_path, json.dumps(sarif_payload, indent=2, ensure_ascii=False))

    step_failed = failed > 0 and fail_on != "none"
    _write_outputs(
        env("GITHUB_OUTPUT"),
        checked=len(results),
        failed=failed,
        valid_flag=not step_failed,
        json_path=written_json,
        sarif_path=written_sarif,
        rule_pack=rule_pack_used,
    )

    print(f"Validated {len(results)} file(s), {failed} failed ({'offline' if engine == 'local' else 'API'}).", file=out)
    return (1 if step_failed else 0), results


if __name__ == "__main__":
    sys.exit(run()[0])
