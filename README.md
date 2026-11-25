# EU e-invoice validator for GitHub Actions (EN 16931, XRechnung, ZUGFeRD)

Validate **UBL 2.1**, **UN/CEFACT CII**, **XRechnung 3.0**, and **ZUGFeRD / Factur-X PDF** invoices against the official **CEN EN 16931** and **KoSIT XRechnung** rule sets in CI.

- **`mode: local` (recommended, 100% offline):** runs the pinned validator container (`ghcr.io/wsystemdev/einvoice-validator:0.1.0-beta.2`) with `--network none`, `--user <uid>:<gid>`, a read-only workspace mount, and baked checksum-verified rules (falling back to the pinned `wsystemdev/einvoice-validator` Python package if Docker/GHCR is unavailable). **No API key is needed and no invoice data leaves your runner.**
- **`mode: api` (default for `@v1` backwards compatibility):** sends files over HTTPS to the W System API (`https://api.wsystem.dev`), keeping all existing `@v1` workflows unchanged.

## Quickstart (offline, no data leaves runner)

```yaml
name: e-invoices
on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate e-invoices offline
        uses: wsystemdev/einvoice-validate-action@v1.1.0
        with:
          mode: local
          files: |
            invoices/**/*.xml
            invoices/**/*.pdf
```

See [`examples/ci-workflow.yml`](examples/ci-workflow.yml) for a full workflow uploading SARIF results to GitHub Code Scanning and saving the JSON report as a workflow artifact.

## Quickstart (`@v1` default API mode)

Existing workflows omitting `mode` continue to call the hosted W System API unchanged:

```yaml
      - uses: wsystemdev/einvoice-validate-action@v1.1.0
        with:
          files: |
            build/invoices/**/*.xml
            build/invoices/**/*.pdf
          # api-key: ${{ secrets.WSYSTEM_API_KEY }}   # optional
```

## Outputs & CI integration

- **Per-file GitHub annotations:** inline `::error` and `::warning` annotations with rule ID, line number (for XML), business terms (`BT-...`), and a plain-English fix hint.
- **Job summary table:** Markdown summary appended to `$GITHUB_STEP_SUMMARY` showing file, status, detected profile, syntax, error/warning counts, and first issue.
- **SARIF 2.1.0 report:** written to `wsystem-einvoice-report.sarif` in `local`/`docker`/`pip` modes (or when `report-format` is set) for `github/codeql-action/upload-sarif`.
- **JSON report:** written to `wsystem-einvoice-report.json` with full machine-readable results.
- **Exit code:** `0` when all files pass according to `fail-on`, `1` when any file fails (or validator invocation fails), `2` on invalid action inputs.

Example annotation:

```text
Error: BR-CO-16: Amount due for payment (BT-115) = Invoice total amount with VAT (BT-112) - Paid amount (BT-113) + Rounding amount (BT-114). [BT-112, BT-113, BT-114, BT-115] Fix: Amount due must be total with VAT minus prepaid amount plus rounding.
```

## Inputs

| Input | Default | Description |
|---|---|---|
| `files` | `""` | Globs, file paths, or directories (newline or comma-separated). Defaults to `**/*.xml` if both `files` and `paths` are empty. |
| `paths` | `""` | Alias for `files`. |
| `mode` | `api` | Execution mode: `api` (default, v1-compatible hosted W System API), `local` (offline Docker container with `--network none`, falling back to pinned `wsystemdev/einvoice-validator` Python package if Docker/GHCR is unavailable), `docker` (strict Docker only), or `pip` (offline Python package). |
| `engine` | `""` | Optional alias for `mode`. |
| `rules` | `auto` | Validation profile: `auto` (detects XRechnung vs EN 16931), `en16931`, or `xrechnung`. |
| `profile` | `""` | Optional alias for `rules`. |
| `fail-on` | `error` | Fail the step on `error`, `warning`, or `none`. |
| `fail-on-warnings` | `false` | Fail the step on warnings (`true` acts like `fail-on: warning`). |
| `license-key` | `""` | Optional W System Validator commercial licence key (`WSV1...`) for commercial rule packs and pro features in `local`/`docker` mode. Passed via an ephemeral `0600` env file and never printed. |
| `rule-pack` | `""` | Optional rule pack ID (for example `2026.11.1`) when using the commercial validator image. |
| `image` | `ghcr.io/wsystemdev/einvoice-validator:0.1.0-beta.2` | Pinned container image used when `mode` is `local` or `docker`. |
| `report-format` | `both` in offline modes / `none` in `api` | Reports to write: `both`, `sarif`, `json`, or `none`. |
| `sarif-report` | `wsystem-einvoice-report.sarif` | Output path for the SARIF 2.1.0 report. |
| `json-report` | `wsystem-einvoice-report.json` | Output path for the JSON report. |
| `fail-on-empty` | `false` | Fail with exit code `2` if no files match `files`/`paths`. |
| `api-key` | `""` | Optional W System API key when `mode: api` (free key: 500 units/month; without one, 20 free calls/day per IP). |
| `api-url` | `https://api.wsystem.dev` | API base URL when `mode: api`. |

## Outputs

| Output | Description |
|---|---|
| `checked` | Number of files checked. |
| `failed` | Number of files that failed according to `fail-on`. |
| `valid` | `'true'` if `failed == 0` (or `fail-on: none`), otherwise `'false'`. |
| `json-report` | Path to the generated JSON report (empty if disabled). |
| `sarif-report` | Path to the generated SARIF 2.1.0 report (empty if disabled). |
| `rule-pack` | Rule pack version used by the validator. |

## Commercial rule packs & self-hosted validator

In `mode: local`, the default free container (`ghcr.io/wsystemdev/einvoice-validator:0.1.0-beta.2`) validates UBL, CII, XRechnung 3.0, and XML extracted from ZUGFeRD/Factur-X PDFs offline with zero keys required.

Teams needing signed rule-pack updates between image releases, multiple rule versions side by side, PDF/A-3 container checks, or a local validation HTTP server can use the commercial W System self-hosted validator image with `license-key` stored as a GitHub Secret:

```yaml
- uses: wsystemdev/einvoice-validate-action@v1.1.0
  with:
    mode: local
    files: invoices/**/*.pdf
    image: ghcr.io/wsystemdev/einvoice-validator-pro:0.1.0-beta.1
    license-key: ${{ secrets.WSYSTEM_VALIDATOR_KEY }}
```

## What is checked & disclaimer

- Checked: XML well-formedness, UBL 2.1 / UN/CEFACT CII D16B XSD schemas, CEN EN 16931 Schematron rules, and KoSIT XRechnung 3.0 rules (including XML extracted from ZUGFeRD / Factur-X PDFs).
- Not checked in the free rule pack: Peppol BIS Billing 3.0 specific rules (`PEPPOL-EN16931-*`).
- **Disclaimer:** This is a technical check against the official rule sets. A technically valid file is not tax or legal advice.

## Licence

- This GitHub Action is licensed under the **Apache License 2.0** (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)).
- In `mode: local`, the validator uses third-party rule artefacts under their own licences (CEN/TC 434 EN 16931 under EUPL-1.2 unmodified; KoSIT XRechnung configuration under Apache-2.0; Saxon-HE under MPL-2.0).

Made by [W System](https://wsystem.dev). Questions: `hello@wsystem.dev`.
