# Safa Lending Cloud v6.2: Product Overview

> LIVE TEST CONTENT: synthetic. Fictional product and companies, for testing only.

Document date: 2026-08-12 | Product version: 6.2

## 1. Overview

Safa Lending Cloud (SLC) is a Shariah-compliant financing origination and servicing platform for Islamic banks and finance houses. Version 6.2 became generally available on 12 August 2026.

## 2. Modules

- Origination: online applications, document collection and a credit workflow with configurable rule-based scorecards, for retail and SME customers.
- Murabaha Financing: asset-based financing with disclosed cost and profit.
- Home Financing: Ijara Muntahia Bittamleek and Diminishing Musharaka.
- Servicing and Collections: repayment schedules, settlements, arrears management and charity-directed late-payment charges.
- Shariah Workflow: product approval, fatwa repository and audit trail.

## 3. Integration

All functions are available through REST APIs documented with OpenAPI 3. Webhooks notify external systems of status changes such as approval and disbursement. Credit bureau and AML screening are connected through configurable adapters to third-party providers.

## 4. Exclusions

- SLC does not support Tawarruq-based or Commodity Murabaha products.
- SLC does not include a general ledger, a core deposit system or a card management system.
