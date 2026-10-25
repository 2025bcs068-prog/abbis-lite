## Honest limitations
- TLS, refresh tokens and per-IP rate limiting are not implemented yet.
- FHIR is mapped on paper only; FHIR endpoints are planned.
- Clinical constants (shelf life, donor interval, required tests) are illustrative and must follow national guidelines.
- Two simultaneous retries of the same idempotency key can hit a unique-constraint error (fix planned).
- A transfer can be approved by a manager of either bank involved; production should require both.

