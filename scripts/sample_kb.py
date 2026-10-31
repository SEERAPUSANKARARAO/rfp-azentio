"""Synthetic knowledge base for the demo. Every document is SAMPLE content about a
fictional product ("Meridian Islamic Banking Suite") and is stamped as such.

Facts here are deliberately consistent so that answers can be checked, with a few
planted problems the agent must catch:

  stale        security_whitepaper_v4_2024, rfp_gulf_2024, sow_gulf_2023 (older than 18 months)
  conflicting  RTO 4h (2024 docs) vs 1h (DR policy v3 / SOC 2); "ISO 27001 certified"
               (2024 whitepaper) vs "certificate expired 31 Jan 2026" (2026 register)
  missing      nothing in the KB on ISO 22301, PCI DSS, UAE PDPL, open-finance APIs
  forbidden    rfp_crescent_2026 (reuse_permitted=False) is the only mention of AI scoring
  roadmap      UAE region is planned, not available
  negative     Sukuk issuance, native Takaful admin, bundled AML engine are explicit exclusions

Block types: ("h", heading) | ("p", paragraph) | ("b", [bullets]).
"""

from __future__ import annotations

STAMP = "SAMPLE CONTENT: synthetic, not real Azentio data. Fictional product for demonstration only."

DOCS: list[dict] = []


def doc(slug, title, kind, fmt, date, blocks, *, version="6.2", status="approved",
        reuse=True, answerable=True, customer=None, owner=None, canonical=False):
    DOCS.append(dict(
        slug=slug, title=title, kind=kind, fmt=fmt, date=date, version=version,
        status=status, reuse_permitted=reuse, answerable=answerable, customer=customer,
        owner=owner, canonical=canonical, blocks=blocks,
    ))


# ===================================================================== product docs
doc("product_overview_v62", "Meridian Islamic Banking Suite v6.2: Product Overview",
    "product_doc", "md", "2026-03-15", [
    ("h", "1. Overview"),
    ("p", "Meridian Islamic Banking Suite (MIBS) is a Shariah-compliant core banking and financing platform "
          "for Islamic banks and for the Islamic windows of conventional banks. Version 6.2 became generally "
          "available on 15 March 2026. Version 5.x reached end of support on 31 December 2025."),
    ("h", "2. Modules"),
    ("b", ["Islamic Financing: Murabaha, Commodity Murabaha, Ijara, Ijara Muntahia Bittamleek, Musharaka, "
           "Diminishing Musharaka, Mudaraba, Istisna and Salam.",
           "Tawarruq and Wakala: organised Tawarruq and Wakala-based investment deposits.",
           "Sukuk Administration and Servicing.",
           "Shariah Governance: rule engine, product approval workflow, fatwa repository, purification.",
           "Profit Rate and Pricing Engine.",
           "Accounts: current accounts (Qard), savings and investment accounts under Mudaraba or Wakala.",
           "Digital Channels APIs."]),
    ("h", "3. Deployment and localisation"),
    ("p", "MIBS is offered as SaaS on AWS in dedicated single-tenant environments. It can also be deployed on "
          "private cloud or on-premises on customer-managed Kubernetes (supported from version 6.0). The platform "
          "is multi-currency, multi-entity and multi-branch. Hijri and Gregorian calendars run in parallel for "
          "repayment schedules, maturities and rental dates. The user interface is available in Arabic and "
          "English with full right-to-left support; further languages are available through localisation packs."),
    ("h", "4. Integration"),
    ("p", "All functions are exposed through REST/JSON APIs described with OpenAPI 3. Event streaming uses "
          "Kafka-compatible topics. ISO 20022 messaging is supported. Payment and SWIFT connectivity is provided "
          "through certified partner adapters. AML/CFT screening and credit bureau checks are performed through "
          "configurable integration adapters to third-party engines."),
    ("h", "5. Exclusions"),
    ("b", ["MIBS does not structure or issue Sukuk. It provides administration and servicing only.",
           "MIBS does not include a native Takaful policy administration system. Takaful is supported by integration.",
           "MIBS does not bundle an AML/CFT screening engine. Screening is by integration with a third-party engine."]),
])

doc("murabaha_guide_v62", "Murabaha and Commodity Financing Guide v6.2", "product_doc", "docx", "2026-03-15", [
    ("h", "1. Scope"),
    ("p", "MIBS supports Murabaha to the purchase orderer (MPO) for vehicles, equipment and goods, and Commodity "
          "Murabaha for working capital."),
    ("h", "2. Lifecycle and Shariah sequencing"),
    ("p", "The lifecycle is: customer promise to purchase, selection of the supplier and asset, purchase of the asset "
          "by the bank, recording of possession, execution of the Murabaha sale with disclosed cost and fixed "
          "mark-up, deferred payment schedule, and settlement. The platform blocks generation of the sale contract "
          "until a purchase invoice and a possession confirmation are attached to the case. This enforces the "
          "Shariah requirement that the bank owns and possesses the asset before selling it to the customer."),
    ("h", "3. Pricing"),
    ("p", "The mark-up is fixed at contract inception and cannot be changed after execution. It may be expressed as "
          "a flat profit amount or as an effective profit rate set at inception. Instalment schedules can follow "
          "the Hijri or the Gregorian calendar."),
    ("h", "4. Early settlement rebate (Ibra')"),
    ("p", "An early settlement rebate (Ibra') can be configured per product under rules approved by the Shariah "
          "board, either as a discretionary rebate or by a published formula. It is not a contractual entitlement "
          "unless the board approves it as such."),
    ("h", "5. Late payment"),
    ("p", "Late-payment charges are handled under gharamah. Any amount above actual cost recovery is routed to a "
          "designated charity account and is never recognised as income. Ta'widh (compensation for actual loss) is "
          "available only where the product rules permit it."),
    ("h", "6. Accounting standards"),
    ("p", "Accounting and reporting follow AAOIFI FAS 28 for Murabaha and other deferred payment sales, as "
          "configured by the institution. IFRS 9 dual reporting is available."),
])

doc("ijara_guide_v62", "Ijara and Ijara Muntahia Bittamleek Guide v6.2", "product_doc", "docx", "2026-03-15", [
    ("h", "1. Scope"),
    ("p", "MIBS supports Ijara (operating lease), Ijara Muntahia Bittamleek (IMB) with transfer of ownership at the "
          "end of the term, and sale-and-lease-back."),
    ("h", "2. Lessor and lessee responsibilities"),
    ("p", "The platform separates ownership costs (major maintenance and Takaful on the asset), which stay with the "
          "lessor, from usage costs, which are borne by the lessee. Rental can be fixed or variable with scheduled "
          "reset dates."),
    ("h", "3. Reference rates"),
    ("p", "Variable rental can be linked to the reference rates EIBOR, SAIBOR and SOFR through rate tables that the "
          "customer maintains or that are populated by a feed adapter."),
    ("h", "4. Asset register and end of term"),
    ("p", "An asset register tracks each leased asset and its depreciation, with straight-line depreciation aligned "
          "to AAOIFI FAS 32 as configured. At the end of an IMB term, ownership transfers by gift, by sale at a "
          "nominal price, or by a phased sale."),
])

doc("partnership_guide_v62", "Musharaka, Diminishing Musharaka and Mudaraba Guide v6.2", "product_doc", "md",
    "2026-03-15", [
    ("h", "1. Musharaka"),
    ("p", "Musharaka partnership financing shares profit at an agreed ratio and shares loss in proportion to "
          "capital contribution."),
    ("h", "2. Diminishing Musharaka"),
    ("p", "In Diminishing Musharaka the bank's share declines as the customer buys units periodically. Rental is "
          "payable on the bank's remaining share. The rental rate can be fixed, or linked to the published reference "
          "rates EIBOR, SAIBOR or SOFR through configurable rate tables."),
    ("p", "Proprietary or custom index benchmarks are not natively supported. A custom index can be used only if the "
          "customer's treasury team maintains its rate table manually. There is no automated feed for custom indices."),
    ("h", "3. Mudaraba investment accounts"),
    ("p", "Mudaraba investment accounts use a profit-sharing ratio (PSR) with pool-based profit distribution and pool "
          "weightages. A profit equalisation reserve (PER) and an investment risk reserve (IRR) are supported."),
])

doc("tawarruq_wakala_guide_v62", "Tawarruq and Wakala Guide v6.2", "product_doc", "md", "2026-03-15", [
    ("h", "1. Organised Tawarruq"),
    ("p", "MIBS orchestrates organised Tawarruq for liquidity and personal finance: purchase of a commodity by the "
          "bank, sale to the customer on deferred terms, and sale by the customer to a third party. Commodity broker "
          "connectivity is provided through configurable adapters. Reverse Tawarruq is available for deposits."),
    ("h", "2. Wakala"),
    ("p", "Wakala (agency) investment deposits and Wakala-based interbank placements are supported from version 6.0. "
          "The agency fee and the expected profit rate are captured on each placement."),
])

doc("sukuk_servicing_guide_v62", "Sukuk Administration and Servicing Guide v6.2", "product_doc", "pdf", "2026-03-20", [
    ("h", "1. Scope"),
    ("p", "MIBS provides Sukuk administration and servicing: the register of holders and positions, calculation and "
          "scheduling of periodic distributions, redemption processing and reporting to the trustee. Servicing is "
          "supported for Ijara, Murabaha, Wakala, Mudaraba and Musharaka Sukuk."),
    ("h", "2. Exclusions"),
    ("p", "MIBS does not structure or issue Sukuk and does not perform arranger, lead manager or trustee functions. "
          "Issuance takes place outside the platform, and the final terms are loaded into MIBS for servicing."),
    ("h", "3. Connectivity"),
    ("p", "Exchange and central securities depository connectivity is provided through partner adapters."),
])

doc("takaful_integration_v62", "Takaful Integration Note v6.2", "product_doc", "md", "2026-02-10", [
    ("h", "1. Position"),
    ("p", "MIBS does not include a native Takaful policy administration system."),
    ("h", "2. Integration"),
    ("p", "Takaful on financed assets is supported through API integration with the customer's Takaful operator "
          "system. MIBS stores the policy reference and can block disbursement until a valid Takaful certificate "
          "reference is attached to the financing case. Premium, coverage and beneficiary data remain in the "
          "operator's system."),
])

doc("shariah_governance_v62", "Shariah Governance and Compliance Guide v6.2", "product_doc", "pdf", "2026-03-15", [
    ("h", "1. Shariah rule engine"),
    ("p", "Product rules are configured from the fatwa that approves the product. Contract templates are versioned "
          "and approved. A rule violation blocks the transaction or raises an exception for review."),
    ("h", "2. Product approval workflow"),
    ("p", "A new product or a change request goes to the Shariah Supervisory Board (SSB) through a maker-checker "
          "workflow. The SSB approval is recorded with the fatwa reference and effective date. Approved products are "
          "version-locked, so a change cannot be applied without a fresh approval."),
    ("h", "3. Fatwa repository"),
    ("p", "Resolutions are held in a searchable repository linked to the products they approve."),
    ("h", "4. Non-compliant income and purification"),
    ("p", "Income identified as not Shariah-compliant, such as late-payment amounts above cost recovery, is "
          "segregated into a charity account. The purification workflow tracks disbursement to approved charities "
          "and produces reports."),
    ("h", "5. Shariah audit"),
    ("p", "An immutable audit trail records every Shariah-relevant action. Exception reports and exports support "
          "internal Shariah audit and external review."),
    ("h", "6. Standards"),
    ("p", "Rules are configurable to the standards the institution adopts, including AAOIFI, IFSB and the central "
          "bank's Shariah governance framework."),
])

# ===================================================================== security
doc("security_whitepaper_v6", "Security and Infrastructure Whitepaper v6", "security_doc", "pdf", "2026-03-25", [
    ("h", "1. Hosting"),
    ("p", "MIBS SaaS runs on AWS in dedicated single-tenant environments for each customer."),
    ("h", "2. Data residency"),
    ("p", "Customer data stays in the contracted region. Generally available regions are AWS ap-south-1 (Mumbai) "
          "for India and AWS eu-central-1 (Frankfurt) for the EU. Production data, backups and replicas stay "
          "inside the contracted residency boundary."),
    ("h", "3. Encryption"),
    ("p", "Data at rest is encrypted with AES-256 using AWS KMS. Customer-managed keys are optional. Data in transit "
          "uses TLS 1.2 or higher, and TLS 1.3 is supported. Service-to-service traffic uses mutual TLS."),
    ("h", "4. Identity and access"),
    ("p", "Single sign-on is available through SAML 2.0 and OIDC. Multi-factor authentication and role-based access "
          "with maker-checker are standard. Vendor staff access to production data uses just-in-time privileged "
          "access that requires customer approval and is session-recorded."),
    ("h", "5. Logging and monitoring"),
    ("p", "Logs are centralised and retained for 13 months, with 90 days immediately searchable. SIEM integration is "
          "supported, and security monitoring runs 24x7."),
    ("h", "6. Vulnerability management and development"),
    ("p", "Vulnerability scans run monthly. The secure development lifecycle includes SAST, DAST and dependency "
          "scanning. For current certification status refer to the Certifications and Attestations Register."),
])

doc("security_whitepaper_v4_2024", "Security and Infrastructure Whitepaper v4", "security_doc", "pdf", "2024-06-10", [
    ("h", "1. Hosting and residency"),
    ("p", "Customer data resides in a single AWS region selected at contract signing: ap-south-1 (Mumbai) or "
          "eu-central-1 (Frankfurt)."),
    ("h", "2. Encryption"),
    ("p", "Data at rest is encrypted with AES-256. Data in transit uses TLS 1.2."),
    ("h", "3. Certifications"),
    ("p", "Meridian is ISO/IEC 27001:2013 certified (certificate valid to 31 January 2026) and undergoes annual "
          "SOC 2 Type II audits."),
    ("h", "4. Resilience"),
    ("p", "Disaster recovery uses cross-availability-zone failover within a single region. The recovery time "
          "objective (RTO) is 4 hours and the recovery point objective (RPO) is 30 minutes."),
], version="5.4")

doc("certifications_register_2026", "Certifications and Attestations Register (as of 30 September 2026)",
    "security_doc", "md", "2026-09-30", [
    ("h", "1. Current status"),
    ("b", ["SOC 2 Type II: current. Report period 1 January 2025 to 31 December 2025, issued 14 February 2026. "
           "The next report is expected in February 2027.",
           "ISO/IEC 27001: the certificate expired on 31 January 2026. A recertification audit (stage 1 and stage 2) "
           "is scheduled for November 2026. Certification must not be described as current until the new "
           "certificate is issued.",
           "CSA STAR Level 1 (self-assessment): published April 2026.",
           "Annual external penetration test: completed May 2026."]),
    ("h", "2. Guidance for bid teams"),
    ("p", "Bid and sales teams must not describe ISO/IEC 27001 as a current certification in any response."),
])

doc("soc2_summary_2026", "SOC 2 Type II Report Summary FY2025", "security_doc", "pdf", "2026-02-14", [
    ("h", "Section 3.1 Security"),
    ("p", "Controls operated effectively with one exception: quarterly access reviews for two privileged groups were "
          "completed 9 days late. The exception was remediated."),
    ("h", "Section 3.2 Availability"),
    ("p", "A multi-region active-passive architecture was introduced in Q3 2025. Disaster recovery failover is tested "
          "annually. The 2025 test measured a recovery time of 52 minutes. The documented objectives are an RTO of "
          "1 hour and an RPO of 5 minutes."),
    ("h", "Section 3.3 Confidentiality"),
    ("p", "Data at rest is encrypted with AES-256 and data in transit with TLS. Regional data residency controls "
          "were tested without exception."),
    ("p", "The report was issued on 14 February 2026 by an independent CPA firm for the period 1 January 2025 to "
          "31 December 2025."),
])

doc("dr_policy_v3", "Disaster Recovery and Business Continuity Policy v3", "security_doc", "pdf", "2026-03-10", [
    ("h", "1. Objectives"),
    ("p", "For production services the recovery time objective (RTO) is 1 hour and the recovery point objective "
          "(RPO) is 5 minutes."),
    ("h", "2. Architecture"),
    ("p", "Production runs multi-region active-passive. Data is replicated automatically between the primary region "
          "and a paired secondary region inside the same residency boundary."),
    ("h", "3. Testing"),
    ("p", "Failover is tested once a year. The most recent test took place on 22 May 2026 and recovered service in "
          "48 minutes."),
])

doc("data_residency_2026", "Data Residency and Regional Availability Note", "security_doc", "md", "2026-04-02", [
    ("h", "1. Generally available regions"),
    ("b", ["India: AWS ap-south-1 (Mumbai).", "EU: AWS eu-central-1 (Frankfurt)."]),
    ("h", "2. Planned regions"),
    ("p", "UAE: AWS me-central-1 is planned, with general availability targeted for Q1 2027. It is not yet available "
          "and is not a contractual commitment. Other regions are considered on request, subject to a feasibility study."),
    ("h", "3. Scope of residency"),
    ("p", "Production data, backups and logs containing personal data stay in the contracted region. Telemetry that "
          "contains no personal data may be processed centrally."),
])

doc("privacy_overview_2025", "Privacy and Data Protection Overview", "policy", "docx", "2025-06-18", [
    ("h", "1. GDPR"),
    ("p", "For GDPR the vendor acts as a processor under a standard data processing agreement. Standard contractual "
          "clauses cover restricted transfers. Personal data breaches are notified to the controller without undue "
          "delay and within 72 hours. A Data Protection Officer is appointed, records of processing are maintained, "
          "and tooling supports data subject requests."),
    ("h", "2. India DPDP Act 2023"),
    ("p", "A gap assessment against the Digital Personal Data Protection Act 2023 was under way in June 2025. No "
          "formal attestation or certification against the Act exists."),
])

doc("pentest_summary_2026", "External Penetration Test Executive Summary 2026", "security_doc", "pdf", "2026-05-05", [
    ("h", "1. Scope and result"),
    ("p", "An independent third party performed the annual external penetration test in April and May 2026 "
          "(report dated 5 May 2026). The scope covered the web application, the APIs and the cloud configuration. "
          "No critical or high findings were open at the report date."),
    ("h", "2. Remediation"),
    ("p", "Two medium and five low findings were remediated within 30 days and closure was confirmed by retest. The "
          "full report is available under NDA."),
])

# ===================================================================== SOWs
doc("sow_gulf_2023", "Statement of Work: Core Banking Modernisation (Gulf Islamic Bank)", "sow", "docx",
    "2023-09-12", [
    ("h", "1. Scope"),
    ("p", "Full core banking migration for Gulf Islamic Bank covering retail, corporate and treasury."),
    ("h", "2. Duration and phases"),
    ("p", "Total duration 11 months: Mobilise 1 month, Design 2 months, Build and configure 4 months, SIT and UAT "
          "2 months, Go-live and hypercare 2 months."),
    ("h", "3. Team"),
    ("p", "Peak vendor team of 14 full-time staff."),
], version="5.2", customer="Gulf Islamic Bank")

doc("sow_alnoor_2025", "Statement of Work: Retail Islamic Lending (Al-Noor Finance)", "sow", "docx", "2025-06-30", [
    ("h", "1. Scope"),
    ("p", "Retail Islamic lending platform for Al-Noor Finance covering Murabaha auto finance, Ijara home finance "
          "and Tawarruq personal finance."),
    ("h", "2. Duration and phases"),
    ("p", "Total duration 8 months: Mobilise 1 month, Design 2 months, Build and configure 3 months, UAT 1 month, "
          "Go-live and hypercare 1 month."),
    ("h", "3. Team and training"),
    ("p", "Peak vendor team of 9 full-time staff. Training comprised three train-the-trainer sessions for the "
          "customer's operations and credit teams."),
], version="6.0", customer="Al-Noor Finance")

doc("sow_pearl_2025", "Statement of Work: Sukuk Servicing Integration (Pearl Investment Bank)", "sow", "docx",
    "2025-11-05", [
    ("h", "1. Scope"),
    ("p", "Integration of MIBS Sukuk servicing with the treasury system and the central securities depository of "
          "Pearl Investment Bank."),
    ("h", "2. Duration and team"),
    ("p", "Total duration 4 months with a peak vendor team of 5 full-time staff."),
], version="6.1", customer="Pearl Investment Bank")

# ===================================================================== past RFP responses
doc("rfp_gulf_2024", "RFP Response: Gulf Islamic Bank Core Modernisation", "past_rfp", "docx", "2024-05-28", [
    ("h", "Q2.1 Data residency"),
    ("p", "Response: Customer data resides in a single AWS region selected at contract signing."),
    ("h", "Q3.2 Disaster recovery objectives"),
    ("p", "Response: The RTO is 4 hours and the RPO is 30 minutes, using cross-availability-zone failover."),
    ("h", "Q4.1 Certifications"),
    ("p", "Response: Meridian is ISO 27001 certified and is audited annually against SOC 2 Type II."),
    ("h", "Q5.3 Availability"),
    ("p", "Response: We commit to 99.9 percent monthly availability."),
    ("h", "Q6.1 Implementation"),
    ("p", "Response: A full core migration for Gulf Islamic Bank is typically completed in 11 months."),
], version="5.4", customer="Gulf Islamic Bank")

doc("rfp_alnoor_2025", "RFP Response: Al-Noor Finance Retail Lending", "past_rfp", "docx", "2025-02-20", [
    ("h", "Q1 Murabaha"),
    ("p", "Response: MIBS supports Murabaha to the purchase orderer and blocks contract generation until the asset "
          "purchase and possession are evidenced."),
    ("h", "Q2 Ijara"),
    ("p", "Response: MIBS supports Ijara and Ijara Muntahia Bittamleek with transfer of ownership by gift, sale or "
          "phased sale."),
    ("h", "Q4 Calendars"),
    ("p", "Response: Hijri and Gregorian calendars run in parallel for schedules and maturities."),
], version="6.0", customer="Al-Noor Finance")

doc("rfp_pearl_2025", "RFP Response: Pearl Investment Bank Sukuk and Treasury", "past_rfp", "docx", "2025-08-14", [
    ("h", "Q1 Sukuk"),
    ("p", "Response: MIBS administers and services Sukuk, covering the holder register, distributions and redemption. "
          "Sukuk issuance is carried out outside MIBS."),
    ("h", "Q2 Wakala"),
    ("p", "Response: Wakala placements are supported, capturing the agency fee and expected profit rate."),
], version="6.1", customer="Pearl Investment Bank")

doc("rfp_crescent_2026", "RFP Response: Crescent Microfinance Digital Lending", "past_rfp", "docx", "2026-01-22", [
    ("h", "Q1 Credit scoring"),
    ("p", "Response: MIBS includes a native AI credit scoring module, ScoreIQ, that scores applicants in real time."),
    ("h", "Q2 Tawarruq"),
    ("p", "Response: Organised Tawarruq with commodity broker integration is available for digital loans."),
    ("p", "Confidential to Crescent Microfinance. Reuse of this response in other bids is not permitted."),
], customer="Crescent Microfinance", reuse=False)

# ===================================================================== policy and glossary
doc("commercial_legal_positions", "Commercial and Legal Standard Positions (internal)", "policy", "md",
    "2026-06-01", [
    ("h", "1. Purpose"),
    ("p", "Internal reference for the Commercial Desk and Legal. None of these positions may be quoted in an RFP "
          "response without approval from the owner."),
    ("h", "2. Commercial"),
    ("p", "Licensing is a tiered subscription based on active accounts. Discounts are approved only by the "
          "Commercial Desk. Payment terms are set per contract."),
    ("h", "3. Legal"),
    ("p", "The standard limitation of liability and indemnity positions are held by Legal (Contracts). Service "
          "credits and availability commitments are contractual and are agreed by Legal and the Commercial Desk."),
], answerable=False, owner="Commercial Desk / Legal (Contracts)")

GLOSSARY = [
    ("Murabaha", ["cost-plus sale", "deferred payment sale", "MPO", "murabahah"],
     "Sale of an asset at disclosed cost plus an agreed mark-up, with payment deferred."),
    ("Ijara", ["lease", "ijarah", "leasing", "IMB", "ijara muntahia bittamleek"],
     "Lease of an asset where the lessor keeps ownership; IMB ends with transfer of ownership."),
    ("Musharaka", ["partnership", "joint venture", "diminishing musharaka", "DM", "declining partnership"],
     "Partnership financing with shared profit and loss."),
    ("Mudaraba", ["investment account", "profit sharing", "PSR", "mudarabah"],
     "Capital provider and manager share profit at an agreed ratio; the capital provider bears loss."),
    ("Tawarruq", ["commodity murabaha", "organised tawarruq", "liquidity finance", "tawarruq"],
     "Purchase of a commodity on deferred terms and sale to a third party to raise cash."),
    ("Wakala", ["agency", "wakalah", "agency deposit"],
     "Agency contract where an agent invests or manages funds for a fee."),
    ("Sukuk", ["islamic bond", "certificates", "sukuk servicing", "sukuk issuance"],
     "Asset-backed Shariah-compliant investment certificates."),
    ("Takaful", ["islamic insurance", "cooperative insurance", "takaful operator"],
     "Cooperative risk-sharing, the Shariah-compliant alternative to conventional insurance."),
    ("Ibra'", ["early settlement rebate", "ibra", "rebate"],
     "Waiver of part of a debt, applied as a rebate on early settlement."),
    ("Gharamah", ["late payment charge", "late fee", "penalty", "ta'widh", "tawidh"],
     "Late-payment charge over actual cost, directed to charity rather than recognised as income."),
    ("Shariah Supervisory Board", ["SSB", "shariah board", "fatwa", "shariah committee"],
     "Body of scholars that approves products and contracts and oversees Shariah compliance."),
    ("Purification", ["non-compliant income", "charity account", "tathir"],
     "Segregation and donation of income that is not Shariah-compliant."),
    ("Riba", ["interest", "usury"], "Prohibited interest or excess in a loan or exchange."),
    ("Hijri", ["islamic calendar", "lunar calendar"], "The Islamic lunar calendar."),
    ("AAOIFI", ["accounting standards", "FAS", "shariah standards"],
     "Accounting and Auditing Organization for Islamic Financial Institutions."),
    # General RFP and banking vocabulary: the same concept is worded differently by bidders and
    # buyers ("roll out" vs "implementation"), which keyword search cannot bridge on its own.
    ("Retail lending", ["consumer financing", "consumer finance", "retail financing", "personal financing",
                        "retail Islamic lending"],
     "Lending to individuals rather than corporates."),
    ("Implementation timeline", ["roll out", "rollout", "go-live", "go live", "time to implement",
                                 "time to deploy", "delivery timeline", "project duration", "duration and phases"],
     "How long a delivery project takes and the phases it runs through."),
    ("Knowledge transfer", ["staff training", "user training", "enablement", "train-the-trainer"],
     "Training provided to the customer's staff."),
    ("Recovery objectives", ["RTO", "RPO", "recovery time", "recovery point", "failover", "region outage"],
     "Targets for how fast and how completely service returns after an outage."),
    ("Data localisation", ["data residency", "in-country data", "data sovereignty", "data localization"],
     "Keeping data inside a given country or region."),
    ("Third-party assurance", ["independent audit", "attestation", "assurance report", "SOC 2", "penetration test"],
     "Independent verification of security and operational controls."),
]

blocks = [("h", "Terms"),
          ("b", [f"{t}: {d} Also referred to as: {', '.join(s)}." for t, s, d in GLOSSARY])]
# A definition is not evidence of what the product does. The glossary drives query expansion
# only and must never be retrievable (or citable) as a source for an answer.
doc("glossary", "Islamic Finance Glossary", "glossary", "md", "2026-01-01", blocks, answerable=False)
