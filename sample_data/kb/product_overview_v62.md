# Meridian Islamic Banking Suite v6.2: Product Overview

> SAMPLE CONTENT: synthetic, not real Azentio data. Fictional product for demonstration only.

Document date: 2026-03-15 | Product version: 6.2

## 1. Overview

Meridian Islamic Banking Suite (MIBS) is a Shariah-compliant core banking and financing platform for Islamic banks and for the Islamic windows of conventional banks. Version 6.2 became generally available on 15 March 2026. Version 5.x reached end of support on 31 December 2025.

## 2. Modules

- Islamic Financing: Murabaha, Commodity Murabaha, Ijara, Ijara Muntahia Bittamleek, Musharaka, Diminishing Musharaka, Mudaraba, Istisna and Salam.
- Tawarruq and Wakala: organised Tawarruq and Wakala-based investment deposits.
- Sukuk Administration and Servicing.
- Shariah Governance: rule engine, product approval workflow, fatwa repository, purification.
- Profit Rate and Pricing Engine.
- Accounts: current accounts (Qard), savings and investment accounts under Mudaraba or Wakala.
- Digital Channels APIs.

## 3. Deployment and localisation

MIBS is offered as SaaS on AWS in dedicated single-tenant environments. It can also be deployed on private cloud or on-premises on customer-managed Kubernetes (supported from version 6.0). The platform is multi-currency, multi-entity and multi-branch. Hijri and Gregorian calendars run in parallel for repayment schedules, maturities and rental dates. The user interface is available in Arabic and English with full right-to-left support; further languages are available through localisation packs.

## 4. Integration

All functions are exposed through REST/JSON APIs described with OpenAPI 3. Event streaming uses Kafka-compatible topics. ISO 20022 messaging is supported. Payment and SWIFT connectivity is provided through certified partner adapters. AML/CFT screening and credit bureau checks are performed through configurable integration adapters to third-party engines.

## 5. Exclusions

- MIBS does not structure or issue Sukuk. It provides administration and servicing only.
- MIBS does not include a native Takaful policy administration system. Takaful is supported by integration.
- MIBS does not bundle an AML/CFT screening engine. Screening is by integration with a third-party engine.
