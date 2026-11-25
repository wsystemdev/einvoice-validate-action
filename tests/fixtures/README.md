# Test Fixtures

All fixtures in this directory are Apache-2.0 licensed:

| Path | Source | Licence |
|---|---|---|
| `valid/KoSIT_*` | KoSIT XRechnung test suite | Apache-2.0 |
| `valid/*_fx.pdf`, `invalid/*_fx.pdf` | Mustangproject ZUGFeRD 2.x samples | Apache-2.0 |
| `valid/ubl-xrechnung-wsystem.xml` | W System | Apache-2.0 |
| `invalid/ubl-xrechnung-*` | W System modifications of KoSIT samples (single rule broken) | Apache-2.0 |
| `invalid/not-an-invoice.xml` | W System (input rejection check) | Apache-2.0 |

`invalid/BASIC-WL_Einfach_fx.pdf` uses the Factur-X BASIC-WL profile, which intentionally omits EN 16931 required fields and therefore fails EN 16931 validation.
