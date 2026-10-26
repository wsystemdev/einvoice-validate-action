# EU e-invoice validator for GitHub Actions (EN 16931, XRechnung)

Catch broken e-invoices before they reach a customer: this action checks the UBL, CII and ZUGFeRD/Factur-X files your code generates against the official CEN EN 16931 Schematron rules, plus the KoSIT XRechnung 3.0 rules for XRechnung files, and fails the build with the rule ID, business terms (BT-…) and a fix hint as inline annotations.

Useful if you generate invoices for Germany (B2B e-invoicing since 2025), Belgium (Peppol since 2026), France (2026–2027), Poland, Spain or Slovakia (2027) and want a regression test on your invoice templates.

```yaml
name: e-invoices
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # generate your test invoices here, e.g. `make invoices`
      - uses: wsystemdev/einvoice-validate-action@v1
        with:
          files: |
            build/invoices/**/*.xml
            build/invoices/**/*.pdf
          # api-key: ${{ secrets.WSYSTEM_API_KEY }}   # optional, see below
```

A failing invoice shows up like this:

```
Error: BR-CO-16: Amount due for payment (BT-115) = Invoice total amount with VAT (BT-112) -Paid amount (BT-113) +Rounding amount (BT-114). [BT-112, BT-113, BT-114, BT-115] Fix: Amount due must be total with VAT minus prepaid amount plus rounding.
```

and the job summary gets a table with one row per file (result, error and warning counts, detected profile).

## Inputs

| input | default | what it does |
|---|---|---|
| `files` | `**/*.xml` | Globs of files to check, one per line or comma-separated. XML (UBL Invoice/CreditNote, CII) or ZUGFeRD/Factur-X PDF. |
| `rules` | `auto` | `auto` picks XRechnung rules for XRechnung files and EN 16931 otherwise; force with `en16931` or `xrechnung`. |
| `api-key` | none | Optional API key, sent as `X-API-Key`. |
| `fail-on-warnings` | `false` | Also fail on warnings (for example the Slovak eFaktúra field checks). |
| `api-url` | `https://api.wsystem.dev` | API base URL. |

Outputs: `checked` (files checked) and `failed` (files that failed or could not be checked).

## Free use and API keys

Without a key, each IP address gets 20 free calls a day. GitHub-hosted runners share IP addresses, so for regular CI use get a [free API key](https://api.wsystem.dev/account/free-key) (500 units a month, no card) and store it as a repository secret. Paid plans are on the [pricing page](https://wsystem.dev/pricing).

## What it does not check

Peppol BIS 3 specific rules (`PEPPOL-EN16931-*`) and the French XP Z12-012 rules are not checked yet. A technically valid file is not tax or legal advice.

## Privacy

Each file is sent over HTTPS to the W System API for checking; see the [privacy notice](https://wsystem.dev/privacy). Use test invoices with dummy parties in CI where you can.

## About

Made by [W System](https://wsystem.dev), which runs the validation API behind this action ([API docs](https://wsystem.dev/docs), [browser validator](https://wsystem.dev/e-invoice-validator)). The action itself is MIT-licensed and uses only the Python standard library already on GitHub runners. Questions: hello@wsystem.dev.
