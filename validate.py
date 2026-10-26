#!/usr/bin/env python3
"""Validate EU e-invoices (UBL, CII, ZUGFeRD/Factur-X PDF) against EN 16931 / XRechnung
with the W System API and report results as GitHub annotations and a job summary.
Standard library only."""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = "wsystem-einvoice-validate-action/1.0"


def env(name, default=""):
    return os.environ.get(name, default).strip()


def collect(patterns):
    files = []
    for line in patterns.replace(",", "\n").splitlines():
        p = line.strip()
        if not p:
            continue
        hits = sorted(glob.glob(p, recursive=True)) if any(c in p for c in "*?[") else ([p] if os.path.isfile(p) else [])
        for f in hits:
            if os.path.isfile(f) and f not in files:
                files.append(f)
    return files


def validate(path, api, rules, key, timeout=60, opener=urllib.request.urlopen):
    with open(path, "rb") as fh:
        body = fh.read()
    ctype = "application/pdf" if body[:5] == b"%PDF-" else "application/xml"
    url = f"{api.rstrip('/')}/einvoice/validate?rules={rules}"
    headers = {"Content-Type": ctype, "User-Agent": UA, "Accept": "application/json"}
    if key:
        headers["X-API-Key"] = key
    last = None
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


def esc(s):
    return str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def run(argv=None, opener=urllib.request.urlopen, out=sys.stdout):
    files = collect(env("INPUT_FILES", "**/*.xml"))
    api = env("INPUT_API_URL", "https://api.wsystem.dev")
    rules = env("INPUT_RULES", "auto") or "auto"
    key = env("INPUT_API_KEY")
    fail_warn = env("INPUT_FAIL_ON_WARNINGS", "false").lower() == "true"
    if not files:
        print("::warning::No files matched the 'files' input; nothing validated.", file=out)
        return 0, []
    rows, failed = [], 0
    for f in files:
        status, d = validate(f, api, rules, key, opener=opener)
        if status == 200 and isinstance(d, dict) and "valid" in d:
            errs, warns = d.get("errors") or [], d.get("warnings") or []
            for e in errs:
                bts = ", ".join(e.get("business_terms") or [])
                msg = f"{e.get('rule_id')}: {e.get('message')}" + (f" [{bts}]" if bts else "") + (f" Fix: {e['fix']}" if e.get("fix") else "")
                print(f"::error file={f},title={esc(e.get('rule_id', 'error'))}::{esc(msg)}", file=out)
            for w in warns:
                print(f"::warning file={f},title={esc(w.get('rule_id', 'warning'))}::{esc(str(w.get('rule_id')) + ': ' + str(w.get('message')))}", file=out)
            bad = (not d["valid"]) or (fail_warn and warns)
            failed += 1 if bad else 0
            rows.append((f, "valid" if d["valid"] else "invalid", len(errs), len(warns), d.get("profile") or d.get("syntax") or ""))
        else:
            failed += 1
            detail = (d or {}).get("detail") or (d or {}).get("error") or ""
            hint = ""
            if status in (402, 429):
                hint = " Free calls for this runner IP are used up: add a free API key (https://api.wsystem.dev/account/free-key) as the 'api-key' input."
            print(f"::error file={f},title=validation call failed::{esc(f'HTTP {status} {detail}'.strip() + hint)}", file=out)
            rows.append((f, f"not checked (HTTP {status})", 0, 0, ""))
    summary = env("GITHUB_STEP_SUMMARY")
    lines = ["### E-invoice validation (EN 16931 / XRechnung)", "", "| file | result | errors | warnings | profile |", "|---|---|---|---|---|"]
    lines += [f"| `{r[0]}` | {r[1]} | {r[2]} | {r[3]} | {r[4]} |" for r in rows]
    lines += ["", "Checked with the [W System e-invoice validation API](https://wsystem.dev/eu-e-invoice-validation-api). Technical check, not tax advice; Peppol BIS 3 specific rules are not checked yet."]
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    gh_out = env("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"checked={len(rows)}\nfailed={failed}\n")
    print(f"Validated {len(rows)} file(s), {failed} failed.", file=out)
    return (1 if failed else 0), rows


if __name__ == "__main__":
    sys.exit(run()[0])
