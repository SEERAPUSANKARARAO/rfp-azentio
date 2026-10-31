# Security Overview 2026

> LIVE TEST CONTENT: synthetic. Fictional product and companies, for testing only.

Document date: 2026-06-18 | Product version: 6.2

## 1. Encryption

- Data at rest is encrypted with AES-256.
- Data in transit is protected with TLS 1.3. TLS 1.2 is accepted only where a customer's integration requires it.
- Encryption keys are held in a managed key service and rotated every 12 months.

## 2. Access control

- Single sign-on is supported through SAML 2.0 and OpenID Connect.
- Multi-factor authentication is mandatory for all administrator accounts.
- Role-based access control separates maker and checker duties.

## 3. Logging

Security and audit logs are retained for 24 months.
