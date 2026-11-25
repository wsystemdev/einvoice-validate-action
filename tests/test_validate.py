import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validate as v


class MockProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Resp:
    def __init__(self, status, payload):
        self.status, self._p = status, payload

    def read(self):
        return json.dumps(self._p).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(payload, status=200, seen=None):
    def op(req, timeout=60):
        if seen is not None:
            seen.append(req)
        if status != 200:
            raise v.urllib.error.HTTPError(
                req.full_url, status, "x", {}, io.BytesIO(json.dumps(payload).encode())
            )
        return Resp(status, payload)

    return op


def docker_runner_for(payload, returncode=0, seen=None, env_snapshots=None):
    def run(cmd, cwd=None, env=None, capture_output=True, text=True):
        if seen is not None:
            seen.append(cmd)
        if env_snapshots is not None and "--env-file" in cmd:
            idx = cmd.index("--env-file")
            ef = cmd[idx + 1]
            env_snapshots.append((ef, pathlib.Path(ef).read_text(encoding="utf-8"), os.stat(ef).st_mode & 0o777))
        return MockProc(returncode=returncode, stdout=json.dumps(payload))

    return run


class TestValidateAction(unittest.TestCase):
    def setUp(self):
        self.orig_cwd = os.getcwd()
        self.orig_env = os.environ.copy()
        self.d = tempfile.mkdtemp()
        os.chdir(self.d)
        self.inv_dir = pathlib.Path(self.d) / "invoices"
        self.inv_dir.mkdir()
        self.xml = self.inv_dir / "a.xml"
        self.xml.write_text("<Invoice/>", encoding="utf-8")
        for k in list(os.environ):
            if k.startswith("INPUT_") or k in (
                "GITHUB_STEP_SUMMARY",
                "GITHUB_OUTPUT",
                "GITHUB_WORKSPACE",
                "WSV_BIN",
                "RUNNER_TEMP",
            ):
                os.environ.pop(k, None)
        os.environ["GITHUB_WORKSPACE"] = self.d
        os.environ["RUNNER_TEMP"] = self.d
        os.environ["INPUT_FILES"] = "invoices/*.xml"

    def tearDown(self):
        os.chdir(self.orig_cwd)
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_v1_api_default_regression(self):
        """Existing @v1 workflows (no mode/engine/report-format set) behave identically: API mode default, no Docker, no extra report files."""
        def forbid_docker(*a, **k):
            raise AssertionError("subprocess/Docker must not be called in default @v1 API mode")

        seen = []
        sum_p = os.path.join(self.d, "sum.md")
        out_p = os.path.join(self.d, "out")
        os.environ["GITHUB_STEP_SUMMARY"] = sum_p
        os.environ["GITHUB_OUTPUT"] = out_p
        os.environ["INPUT_RULES"] = "auto"
        os.environ["INPUT_API_URL"] = "https://api.wsystem.dev"

        # 1. Valid pass with no API key
        out1 = io.StringIO()
        code1, _ = v.run(
            opener=opener_for({"valid": True, "errors": [], "warnings": [], "profile": "XRechnung"}, seen=seen),
            runner=forbid_docker,
            out=out1,
        )
        self.assertEqual(code1, 0)
        self.assertEqual(seen[0].full_url, "https://api.wsystem.dev/einvoice/validate?rules=auto")
        self.assertIsNone(seen[0].get_header("X-api-key"))
        self.assertEqual(seen[0].get_header("Content-type"), "application/xml")
        self.assertIn("XRechnung", pathlib.Path(sum_p).read_text())
        self.assertIn("checked=1\nfailed=0\n", pathlib.Path(out_p).read_text())
        # No unexpected SARIF/JSON files written in default v1 API mode
        self.assertFalse((pathlib.Path(self.d) / "wsystem-einvoice-report.json").exists())
        self.assertFalse((pathlib.Path(self.d) / "wsystem-einvoice-report.sarif").exists())

        # 2. Invalid invoice emits v1 annotation and exits 1
        out2 = io.StringIO()
        p_err = {
            "valid": False,
            "errors": [{"rule_id": "BR-CO-16", "message": "Amount due", "business_terms": ["BT-115"], "fix": "Fix it"}],
            "warnings": [],
        }
        code2, _ = v.run(opener=opener_for(p_err), runner=forbid_docker, out=out2)
        self.assertEqual(code2, 1)
        self.assertIn("::error file=invoices/a.xml,title=BR-CO-16::BR-CO-16: Amount due [BT-115] Fix: Fix it", out2.getvalue())

        # 3. Warnings only fail with INPUT_FAIL_ON_WARNINGS=true
        p_warn = {"valid": True, "errors": [], "warnings": [{"rule_id": "W1", "message": "m"}]}
        self.assertEqual(v.run(opener=opener_for(p_warn), runner=forbid_docker, out=io.StringIO())[0], 0)
        os.environ["INPUT_FAIL_ON_WARNINGS"] = "true"
        self.assertEqual(v.run(opener=opener_for(p_warn), runner=forbid_docker, out=io.StringIO())[0], 1)

    def test_local_offline_docker_and_reports(self):
        os.environ["INPUT_MODE"] = "local"
        seen = []
        payload = {
            "validator": {"name": "W System e-invoice validator", "edition": "free", "version": "0.1.0"},
            "rule_pack": "2026.11.1",
            "checked": 1,
            "failed": 0,
            "files": [
                {
                    "file": "invoices/a.xml",
                    "valid": True,
                    "profile": "XRechnung",
                    "syntax": "ubl",
                    "error_count": 0,
                    "warning_count": 0,
                    "errors": [],
                    "warnings": [],
                }
            ],
        }
        sum_p = os.path.join(self.d, "summary.md")
        out_p = os.path.join(self.d, "gh_out")
        os.environ["GITHUB_STEP_SUMMARY"] = sum_p
        os.environ["GITHUB_OUTPUT"] = out_p

        def forbid_net(*a, **k):
            raise AssertionError("network opener must not be called in local mode")

        out = io.StringIO()
        code, rows = v.run(opener=forbid_net, runner=docker_runner_for(payload, seen=seen), out=out)
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 1)
        cmd = seen[0]
        self.assertEqual(cmd[:5], ["docker", "run", "--rm", "--network", "none"])
        self.assertIn("--user", cmd)
        self.assertIn(f"{pathlib.Path(self.d).resolve()}:/work:ro", cmd)
        self.assertIn("ghcr.io/wsystemdev/einvoice-validator:0.1.0-beta.2", cmd)
        self.assertIn("invoices/a.xml", cmd)

        rep_json = json.loads((pathlib.Path(self.d) / "wsystem-einvoice-report.json").read_text())
        self.assertTrue(rep_json["offline"])
        self.assertEqual(rep_json["rule_pack"], "2026.11.1")
        self.assertEqual(rep_json["checked"], 1)
        self.assertEqual(rep_json["failed"], 0)

        rep_sarif = json.loads((pathlib.Path(self.d) / "wsystem-einvoice-report.sarif").read_text())
        self.assertEqual(rep_sarif["version"], "2.1.0")
        self.assertEqual(rep_sarif["runs"][0]["results"], [])

        gh_out = pathlib.Path(out_p).read_text()
        self.assertIn("checked=1", gh_out)
        self.assertIn("failed=0", gh_out)
        self.assertIn("valid=true", gh_out)
        self.assertIn("rule-pack=2026.11.1", gh_out)
        self.assertIn("XRechnung", pathlib.Path(sum_p).read_text())

    def test_paths_profile_annotations_and_sarif_lines(self):
        os.environ["INPUT_MODE"] = "local"
        pdf = self.inv_dir / "b.pdf"
        pdf.write_bytes(b"%PDF-1.7 test")
        os.environ.pop("INPUT_FILES", None)
        os.environ["INPUT_PATHS"] = "invoices/*.xml, invoices/*.pdf"
        os.environ["INPUT_PROFILE"] = "xrechnung"

        seen = []
        payload = {
            "rule_pack": "2026.11.1",
            "files": [
                {
                    "file": "invoices/a.xml",
                    "valid": False,
                    "profile": "XRechnung",
                    "syntax": "ubl",
                    "error_count": 1,
                    "warning_count": 0,
                    "errors": [
                        {
                            "rule_id": "BR-CO-16",
                            "message": "Amount due mismatch\nline2",
                            "business_terms": ["BT-112", "BT-115"],
                            "fix": "Recalculate BT-115",
                            "line": 42,
                            "ruleset": "EN16931",
                        }
                    ],
                    "warnings": [],
                },
                {
                    "file": "invoices/b.pdf",
                    "valid": False,
                    "profile": "Factur-X/ZUGFeRD BASICWL",
                    "syntax": "cii",
                    "error_count": 1,
                    "warning_count": 0,
                    "errors": [{"rule_id": "BR-16", "message": "Missing VAT breakdown"}],
                    "warnings": [],
                },
            ],
        }
        out = io.StringIO()
        code, _ = v.run(runner=docker_runner_for(payload, returncode=1, seen=seen), out=out)
        self.assertEqual(code, 1)
        self.assertIn("--rules", seen[0])
        self.assertEqual(seen[0][seen[0].index("--rules") + 1], "xrechnung")

        stdout = out.getvalue()
        self.assertIn("::error file=invoices/a.xml,line=42,title=BR-CO-16::BR-CO-16: Amount due mismatch%0Aline2 [BT-112, BT-115] Fix: Recalculate BT-115", stdout)
        self.assertIn("::error file=invoices/b.pdf,title=BR-16::", stdout)

        sarif = json.loads((pathlib.Path(self.d) / "wsystem-einvoice-report.sarif").read_text())
        results = sarif["runs"][0]["results"]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["ruleId"], "BR-CO-16")
        self.assertEqual(results[0]["locations"][0]["physicalLocation"]["region"]["startLine"], 42)
        self.assertNotIn("region", results[1]["locations"][0]["physicalLocation"])

    def test_fail_on_modes(self):
        os.environ["INPUT_MODE"] = "local"
        payload_warn = {
            "rule_pack": "2026.11.1",
            "files": [
                {
                    "file": "invoices/a.xml",
                    "valid": True,
                    "profile": "XRechnung",
                    "syntax": "ubl",
                    "error_count": 0,
                    "warning_count": 1,
                    "errors": [],
                    "warnings": [{"rule_id": "BR-DE-TMP-32", "message": "Warning msg"}],
                }
            ],
        }
        self.assertEqual(v.run(runner=docker_runner_for(payload_warn, 0), out=io.StringIO())[0], 0)

        os.environ["INPUT_FAIL_ON"] = "warning"
        self.assertEqual(v.run(runner=docker_runner_for(payload_warn, 0), out=io.StringIO())[0], 1)

        os.environ["INPUT_FAIL_ON"] = "error"
        os.environ["INPUT_FAIL_ON_WARNINGS"] = "true"
        self.assertEqual(v.run(runner=docker_runner_for(payload_warn, 0), out=io.StringIO())[0], 1)

        os.environ.pop("INPUT_FAIL_ON_WARNINGS", None)
        os.environ["INPUT_FAIL_ON"] = "none"
        payload_err = {
            "rule_pack": "2026.11.1",
            "files": [
                {
                    "file": "invoices/a.xml",
                    "valid": False,
                    "error_count": 1,
                    "warning_count": 0,
                    "errors": [{"rule_id": "BR-1", "message": "bad"}],
                    "warnings": [],
                }
            ],
        }
        out_p = os.path.join(self.d, "gh_out")
        os.environ["GITHUB_OUTPUT"] = out_p
        code, _ = v.run(runner=docker_runner_for(payload_err, 1), out=io.StringIO())
        self.assertEqual(code, 0)
        gh_out = pathlib.Path(out_p).read_text()
        self.assertIn("failed=1", gh_out)
        self.assertIn("valid=true", gh_out)

    def test_license_key_env_file_not_leaked_and_cleaned(self):
        os.environ["INPUT_MODE"] = "local"
        secret = "WSV1.eyJwbGFuIjoidGVhbSJ9.TOP_SECRET_SIG_XYZ"
        os.environ["INPUT_LICENSE_KEY"] = secret
        os.environ["INPUT_IMAGE"] = "ghcr.io/wsystemdev/einvoice-validator-pro:0.1.0-beta.1"
        os.environ["INPUT_RULE_PACK"] = "2026.11.1"
        seen, snaps = [], []
        payload = {
            "rule_pack": "2026.11.1",
            "files": [{"file": "invoices/a.xml", "valid": True, "errors": [], "warnings": []}],
        }
        out = io.StringIO()
        code, _ = v.run(runner=docker_runner_for(payload, 0, seen=seen, env_snapshots=snaps), out=out)
        self.assertEqual(code, 0)
        self.assertEqual(len(snaps), 1)
        env_path, env_contents, mode = snaps[0]
        self.assertEqual(mode, 0o600)
        self.assertEqual(env_contents, f"WSV_LICENSE_KEY={secret}\n")
        self.assertFalse(os.path.exists(env_path))
        self.assertIn("--pack", seen[0])
        self.assertNotIn(secret, " ".join(seen[0]))
        self.assertNotIn(secret, out.getvalue())
        self.assertNotIn(secret, (pathlib.Path(self.d) / "wsystem-einvoice-report.json").read_text())
        self.assertNotIn(secret, (pathlib.Path(self.d) / "wsystem-einvoice-report.sarif").read_text())

    def test_local_falls_back_to_pip_when_ghcr_unauthorized_while_docker_mode_strict(self):
        os.environ["INPUT_MODE"] = "local"
        cache = pathlib.Path(self.d) / "wsv-offline-v0.1.0-beta.2"
        py_bin = cache / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        py_bin.parent.mkdir(parents=True)
        py_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        (cache / "rules" / "2026-08-31").mkdir(parents=True)
        (cache / "rules" / "2026-08-31" / ".ok").write_text("ok", encoding="utf-8")

        calls = []

        def fallback_runner(cmd, cwd=None, env=None, capture_output=True, text=True):
            calls.append((cmd, env))
            if cmd[0] == "docker":
                return MockProc(returncode=125, stdout="", stderr="Error response from daemon: unauthorized")
            return MockProc(
                returncode=0,
                stdout=json.dumps({"rule_pack": "2026.11.1", "files": [{"file": "invoices/a.xml", "valid": True, "errors": [], "warnings": []}]}),
            )

        out = io.StringIO()
        code, _ = v.run(runner=fallback_runner, out=out)
        self.assertEqual(code, 0)
        self.assertIn("::notice::Container image", out.getvalue())
        self.assertEqual(calls[0][0][0], "docker")
        self.assertEqual(calls[1][0][0], str(py_bin))
        self.assertEqual(calls[1][1]["WSV_RULES_DIR"], str(cache / "rules"))

        # Strict mode=docker does not fall back
        os.environ["INPUT_MODE"] = "docker"
        out2 = io.StringIO()
        code2, _ = v.run(runner=fallback_runner, out=out2)
        self.assertEqual(code2, 1)
        self.assertIn("unauthorized", out2.getvalue())

    def test_input_error_and_docker_failure(self):
        os.environ["INPUT_MODE"] = "local"
        payload = {
            "rule_pack": "2026.11.1",
            "files": [{"file": "invoices/a.xml", "valid": False, "input_error": "not an e-invoice XML", "errors": [], "warnings": []}],
        }
        out = io.StringIO()
        code, _ = v.run(runner=docker_runner_for(payload, 1), out=out)
        self.assertEqual(code, 1)
        self.assertIn("title=not an e-invoice", out.getvalue())
        sarif = json.loads((pathlib.Path(self.d) / "wsystem-einvoice-report.sarif").read_text())
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "INPUT_ERROR")

        os.environ["INPUT_MODE"] = "docker"

        def fail_docker(*a, **k):
            return MockProc(returncode=125, stdout="", stderr="pull access denied")

        out2 = io.StringIO()
        code2, _ = v.run(runner=fail_docker, out=out2)
        self.assertEqual(code2, 1)
        self.assertIn("pull access denied", out2.getvalue())

    def test_no_files_and_invalid_inputs(self):
        os.environ["INPUT_FILES"] = "invoices/*.none"
        out = io.StringIO()
        self.assertEqual(v.run(out=out)[0], 0)
        self.assertIn("::warning::", out.getvalue())

        os.environ["INPUT_FAIL_ON_EMPTY"] = "true"
        self.assertEqual(v.run(out=io.StringIO())[0], 2)

        os.environ["INPUT_FAIL_ON"] = "bogus"
        self.assertEqual(v.run(out=io.StringIO())[0], 2)

    def test_report_format_options(self):
        os.environ["INPUT_MODE"] = "local"
        payload = {"rule_pack": "2026.11.1", "files": [{"file": "invoices/a.xml", "valid": True, "errors": [], "warnings": []}]}
        os.environ["INPUT_REPORT_FORMAT"] = "sarif"
        os.environ["INPUT_SARIF_REPORT"] = "out/custom.sarif"
        v.run(runner=docker_runner_for(payload, 0), out=io.StringIO())
        self.assertTrue((pathlib.Path(self.d) / "out/custom.sarif").is_file())
        self.assertFalse((pathlib.Path(self.d) / "wsystem-einvoice-report.json").exists())

    def test_api_engine_compat(self):
        os.environ["INPUT_ENGINE"] = "api"
        os.environ["INPUT_API_KEY"] = "k123"
        self.xml.write_bytes(b"%PDF-1.7 ...")
        seen, out = [], io.StringIO()
        code, _ = v.run(opener=opener_for({"detail": "free tier used"}, status=429, seen=seen), out=out)
        self.assertEqual(code, 1)
        self.assertEqual(seen[0].get_header("X-api-key"), "k123")
        self.assertEqual(seen[0].get_header("Content-type"), "application/pdf")
        self.assertIn("free-key", out.getvalue())

    def test_yaml_files_parse(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        root = pathlib.Path(__file__).resolve().parent.parent
        for rel in ("action.yml", "examples/ci-workflow.yml", ".github/workflows/test.yml"):
            with open(root / rel, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            self.assertIsInstance(doc, dict, rel)


if __name__ == "__main__":
    unittest.main()
