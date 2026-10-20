# ABBIS-Lite

A working technical slice of an interoperable blood-bank information system for Africa,
built for  operate within complex health systems where information, infrastructure and operational processes vary between institutions, regions and countries.

**Scope:** donor → collection → lab screening → inventory → hospital request → issue,
with unit-level traceability, offline-first sync, a FHIR-aligned data model, and
AI demand/expiry forecasting with human approval.

**Data:** synthetic only. No real donor or patient records.

## Run
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python -m data.generate_synthetic
    uvicorn app.main:app --reload
