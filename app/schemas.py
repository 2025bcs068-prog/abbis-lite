from pydantic import BaseModel, Field

class CollectIn(BaseModel):
    client_op_id: str
    donor_code: str
    bank_id: str
    component: str = "whole_blood"
    unit_code: str | None = None  # facility-assigned, so offline clients can label units before syncing

class ScreenIn(BaseModel):
    client_op_id: str
    results: dict[str, str]  # {"HIV": "negative", "HBV": "negative", ...}

class RequestIn(BaseModel):
    client_op_id: str
    hospital: str
    bank_id: str
    blood_group: str
    units: int = Field(ge=1, le=20)

class DonorIn(BaseModel):
    client_op_id: str
    code: str
    blood_group: str
    birth_year: int = Field(ge=1950, le=2010)
    bank_id: str
