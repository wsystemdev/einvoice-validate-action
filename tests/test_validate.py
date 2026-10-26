import io, json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import validate as v


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
            raise v.urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(json.dumps(payload).encode()))
        return Resp(status, payload)
    return op


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.f = os.path.join(self.d, "a.xml")
        open(self.f, "w").write("<Invoice/>")
        os.environ["INPUT_FILES"] = os.path.join(self.d, "*.xml")
        for k in ("INPUT_API_KEY", "GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "INPUT_FAIL_ON_WARNINGS"):
            os.environ.pop(k, None)

    def test_valid_passes_and_no_key_header(self):
        seen, out = [], io.StringIO()
        code, rows = v.run(opener=opener_for({"valid": True, "errors": [], "warnings": []}, seen=seen), out=out)
        self.assertEqual(code, 0)
        self.assertIsNone(seen[0].get_header("X-api-key"))
        self.assertEqual(seen[0].get_header("Content-type"), "application/xml")

    def test_errors_annotate_and_fail(self):
        out = io.StringIO()
        p = {"valid": False, "errors": [{"rule_id": "BR-CO-16", "message": "Amount due", "business_terms": ["BT-115"], "fix": "Fix it"}], "warnings": []}
        code, _ = v.run(opener=opener_for(p), out=out)
        self.assertEqual(code, 1)
        self.assertIn("::error file=", out.getvalue())
        self.assertIn("BR-CO-16", out.getvalue())

    def test_warnings_only_fail_when_asked(self):
        p = {"valid": True, "errors": [], "warnings": [{"rule_id": "W1", "message": "m"}]}
        self.assertEqual(v.run(opener=opener_for(p), out=io.StringIO())[0], 0)
        os.environ["INPUT_FAIL_ON_WARNINGS"] = "true"
        self.assertEqual(v.run(opener=opener_for(p), out=io.StringIO())[0], 1)

    def test_quota_hint_and_key_header(self):
        os.environ["INPUT_API_KEY"] = "k123"
        seen, out = [], io.StringIO()
        code, _ = v.run(opener=opener_for({"detail": "free tier used"}, status=429, seen=seen), out=out)
        self.assertEqual(code, 1)
        self.assertEqual(seen[0].get_header("X-api-key"), "k123")
        self.assertIn("free-key", out.getvalue())

    def test_summary_and_outputs(self):
        s, o = os.path.join(self.d, "sum.md"), os.path.join(self.d, "out")
        os.environ["GITHUB_STEP_SUMMARY"], os.environ["GITHUB_OUTPUT"] = s, o
        v.run(opener=opener_for({"valid": True, "errors": [], "warnings": [], "profile": "XRechnung"}), out=io.StringIO())
        self.assertIn("XRechnung", open(s).read())
        self.assertIn("checked=1", open(o).read())

    def test_pdf_content_type(self):
        open(self.f, "wb").write(b"%PDF-1.7 ...")
        seen = []
        v.run(opener=opener_for({"valid": True, "errors": [], "warnings": []}, seen=seen), out=io.StringIO())
        self.assertEqual(seen[0].get_header("Content-type"), "application/pdf")

    def test_no_files_is_warning(self):
        os.environ["INPUT_FILES"] = os.path.join(self.d, "*.none")
        out = io.StringIO()
        self.assertEqual(v.run(opener=opener_for({}), out=out)[0], 0)
        self.assertIn("::warning::", out.getvalue())


if __name__ == "__main__":
    unittest.main()
