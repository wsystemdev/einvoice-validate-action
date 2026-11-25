# EU E-Invoice Validator for GitHub Actions (W System)

Validate **UBL 2.1**, **UN/CEFACT CII**, **XRechnung 3.0**, and **ZUGFeRD / Factur-X PDF** invoices against the official **CEN EN 16931** and **KoSIT XRechnung** rule sets in CI — **100% offline**.

In default `local` mode, the validator container runs with `--network none` and baked, checksum-verified rules. No API key is needed and **no invoice data ever leaves your runner**.

## Quickstart

```yaml
name: e-invoices
on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate e-invoices offline
        uses: wsystemdev/einvoice-validate-action@v2
        with:
          files: |
            invoices/**/*.xml
            invoices/**/*.pdf
```

See [`examples/ci-workflow.yml`](examples/ci-workflow.yml) for a full workflow uploading SARIF results to GitHub Code Scanning and saving the JSON report as a workflow artifact.

## Outputs & CI integration

- **Per-file GitHub annotations:** inline `::error` and `::warning` annotations with rule ID, line number (for XML), business terms (`BT-...`), and a plain-English fix hint.
- **Job summary table:** Markdown summary appended to `$GITHUB_STEP_SUMMARY` showing file, status, detected profile, syntax, error/warning counts, and first issue.
- **SARIF 2.1.0 report:** written to `wsystem-einvoice-report.sarif` by default for `github/codeql-action/upload-sarif`.
- **JSON report:** written to `wsystem-einvoice-report.json` by default with full machine-readable results.
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
| `rules` | `auto` | Validation profile: `auto` (detects XRechnung vs EN 16931), `en16931`, or `xrechnung`. |
| `profile` | `""` | Optional alias for `rules`. |
| `fail-on` | `error` | Fail the step on `error`, `warning`, or `none`. |
| `fail-on-warnings` | `false` | Deprecated boolean alias (`true` acts like `fail-on: warning`). |
| `license-key` | `""` | Optional W System Validator commercial licence key (`WSV1...`) for commercial rule packs and pro features. Passed via an ephemeral `0600` env file and never printed. |
| `rule-pack` | `""` | Optional rule pack ID (for example `2026.11.1`) when using the commercial validator image. |
| `engine` | `local` | `local` (offline Docker container with `--network none`) or `api` (hosted W System API for non-Docker runners). |
| `image` | `ghcr.io/wsystemdev/einvoice-validator:0.1` | Container image used when `engine` is `local`. |
| `report-format` | `both` | Reports to write: `both`, `sarif`, `json`, or `none`. |
| `sarif-report` | `wsystem-einvoice-report.sarif` | Output path for the SARIF 2.1.0 report. |
| `json-report` | `wsystem-einvoice-report.json` | Output path for the JSON report. |
| `fail-on-empty` | `false` | Fail with exit code `2` if no files match `files`/`paths`. |
| `api-key` | `""` | Optional W System API key (only used when `engine: api`). |
| `api-url` | `https://api.wsystem.dev` | API base URL (only used when `engine: api`). |

## Outputs

| Output | Description |
|---|---|
| `checked` | Number of files checked. |
| `failed` | Number of files that failed according to `fail-on`. |
| `valid` | `'true'` if `failed == 0`, otherwise `'false'`. |
| `json-report` | Path to the generated JSON report (empty if disabled). |
| `sarif-report` | Path to the generated SARIF 2.1.0 report (empty if disabled). |
| `rule-pack` | Rule pack version used by the validator. |

## Commercial rule packs & self-hosted validator

The default free container (`ghcr.io/wsystemdev/einvoice-validator`) validates UBL, CII, XRechnung 3.0, and XML extracted from ZUGFeRD/Factur-X PDFs offline with zero keys required.

Teams needing signed rule-pack updates between image releases, multiple rule versions side by side, PDF/A-3 container checks, or a local validation HTTP server can use the commercial W System self-hosted validator image with `license-key` stored as a GitHub Secret:

```yaml
- uses: wsystemdev/einvoice-validate-action@v2
  with:
    files: invoices/**/*.pdf
    image: ghcr.io/wsystemdev/einvoice-validator-pro:0.1
    license-key: ${{ secrets.WSYSTEM_VALIDATOR_KEY }}
```

## Hosted API fallback (`engine: api`)

On runners without Docker (such as macOS or Windows) or if you used `@v1`, you can opt into `engine: api`, which sends files over HTTPS to `https://api.wsystem.dev`. In default `engine: local` mode, nothing is sent over the network.

## What is checked & disclaimer

- Checked: XML well-formedness, UBL 2.1 / UN/CEFACT CII D16B XSD schemas, CEN EN 16931 Schematron rules, and KoSIT XRechnung 3.0 rules (including XML extracted from ZUGFeRD / Factur-X PDFs).
- Not checked in the free rule pack: Peppol BIS Billing 3.0 specific rules (`PEPPOL-EN16931-*`).
- **Disclaimer:** This is a technical check against the official rule sets. A technically valid file is not tax or legal advice.

## Licence

- This GitHub Action is licensed under the **Apache License 2.0** (see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE)).
- In `engine: local` mode, the validator container ships third-party rule artefacts under their own licences (CEN/TC 434 EN 16931 under EUPL-1.2 unmodified; KoSIT XRechnung configuration under Apache-2.0; Saxon-HE under MPL-2.0).

Made by [W System](https://wsystem.dev). Questions: `hello@wsystem.dev`.
