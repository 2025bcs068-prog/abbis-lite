"""Generate a year of SYNTHETIC blood-bank data. No real donor/patient data."""
import random
from datetime import date, datetime, timedelta
from app.db import Base, engine, SessionLocal
from app.models import (BloodBank, Donor, BloodUnit, ScreeningResult,
                        HospitalRequest, uid)

random.seed(42)
GROUPS = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"]
WEIGHTS = [0.47, 0.25, 0.20, 0.03, 0.02, 0.01, 0.01, 0.01]  # illustrative, not clinical data
BANKS = [("Mbarara Regional Blood Bank", "Western", "Uganda"),
         ("Mulago National Blood Bank", "Central", "Uganda"),
         ("Gulu Regional Blood Bank", "Northern", "Uganda"),
         ("Mbale Regional Blood Bank", "Eastern", "Uganda")]
HOSPITALS = ["Referral Hospital A", "District Hospital B", "Health Centre IV C", "Teaching Hospital D"]
SHELF_LIFE_DAYS = 35  # whole blood, illustrative

def main(days: int = 365):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    today = date.today()
    with SessionLocal() as s:
        banks = [BloodBank(name=n, region=r, country=c) for n, r, c in BANKS]
        s.add_all(banks); s.flush()

        donors = []
        for i in range(1200):
            d = Donor(code=f"D{i:05d}", blood_group=random.choices(GROUPS, WEIGHTS)[0],
                      birth_year=random.randint(1970, 2007), bank_id=random.choice(banks).id)
            donors.append(d)
        s.add_all(donors); s.flush()

        n_units = 0
        for offset in range(days, 0, -1):
            day = today - timedelta(days=offset)
            for bank in banks:
                for _ in range(random.randint(8, 20)):
                    donor = random.choice([d for d in donors if d.bank_id == bank.id])
                    n_units += 1
                    positive = random.random() < 0.03
                    expired = day + timedelta(days=SHELF_LIFE_DAYS) < today
                    status = "discarded" if positive else ("discarded" if expired and random.random() < 0.15 else "available")
                    u = BloodUnit(unit_code=f"U{n_units:08d}", donor_id=donor.id, bank_id=bank.id,
                                  blood_group=donor.blood_group, collected_on=day,
                                  expires_on=day + timedelta(days=SHELF_LIFE_DAYS), status=status)
                    s.add(u); s.flush()
                    for t in ["HIV", "HBV", "HCV", "Syphilis"]:
                        s.add(ScreeningResult(unit_id=u.id, test=t, tested_at=datetime.combine(day, datetime.min.time()),
                                              result="positive" if (positive and t == "HBV") else "negative"))
                    donor.last_donation = day

                # hospital demand: weekly seasonality + slow upward trend + noise
                for g, w in zip(GROUPS, WEIGHTS):
                    base = 14 * w * (1.25 if day.weekday() < 5 else 0.8) * (1 + 0.1 * (days - offset) / days)
                    qty = max(0, round(random.gauss(base, max(0.5, base * 0.3))))
                    if qty:
                        s.add(HospitalRequest(hospital=random.choice(HOSPITALS), bank_id=bank.id, blood_group=g,
                                              units_requested=qty, requested_at=datetime.combine(day, datetime.min.time()),
                                              status="fulfilled"))
        s.commit()
    print(f"Generated {n_units} units across {len(BANKS)} banks over {days} days.")

if __name__ == "__main__":
    main()
