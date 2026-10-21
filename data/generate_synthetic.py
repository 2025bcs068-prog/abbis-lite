"""Generate a year of SYNTHETIC blood-bank data. No real donor/patient data."""
import random
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.db import Base, SessionLocal, engine
from app.models import BloodBank, BloodUnit, Donor, HospitalRequest, ScreeningResult, uid

random.seed(42)
GROUPS = ["O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-"]
WEIGHTS = [0.47, 0.25, 0.20, 0.03, 0.02, 0.01, 0.01, 0.01]  # illustrative, not clinical data
BANKS = [("Mbarara Regional Blood Bank", "Western", "Uganda"),
         ("Mulago National Blood Bank", "Central", "Uganda"),
         ("Gulu Regional Blood Bank", "Northern", "Uganda"),
         ("Mbale Regional Blood Bank", "Eastern", "Uganda")]
HOSPITALS = ["Referral Hospital A", "District Hospital B", "Health Centre IV C", "Teaching Hospital D"]
SHELF_LIFE_DAYS = 35
MIN_INTERVAL = 56
EXPIRY_WASTE_RATE = 0.15  # share of aged units lost to expiry (illustrative)


def main(days: int = 365):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    today = date.today()
    with SessionLocal() as s:
        banks = [BloodBank(id=uid(), name=n, region=r, country=c) for n, r, c in BANKS]
        s.add_all(banks)
        donors = [Donor(id=uid(), code=f"D{i:05d}", blood_group=random.choices(GROUPS, WEIGHTS)[0],
                        birth_year=random.randint(1970, 2007), bank_id=banks[i % len(banks)].id)
                  for i in range(5000)]
        s.add_all(donors)
        s.flush()

        pool = {b.id: [d for d in donors if d.bank_id == b.id] for b in banks}  # eligible donors
        returning = defaultdict(list)                                           # date -> donors becoming eligible
        n_units = 0
        for offset in range(days, 0, -1):
            day = today - timedelta(days=offset)
            for bank_id, donor in returning.pop(day, []):
                pool[bank_id].append(donor)
            for bank in banks:
                p = pool[bank.id]
                for _ in range(min(random.randint(8, 20), len(p))):
                    i = random.randrange(len(p))
                    p[i], p[-1] = p[-1], p[i]
                    donor = p.pop()                                  # respects the 56-day rule
                    returning[day + timedelta(days=MIN_INTERVAL)].append((bank.id, donor))
                    donor.last_donation = day
                    n_units += 1
                    expires = day + timedelta(days=SHELF_LIFE_DAYS)
                    positive = random.random() < 0.03
                    if positive:
                        status = "discarded"
                    elif expires >= today:
                        status = "available"
                    else:
                        status = "discarded" if random.random() < EXPIRY_WASTE_RATE else "issued"
                    unit = BloodUnit(id=uid(), unit_code=f"U{n_units:08d}", donor_id=donor.id, bank_id=bank.id,
                                     blood_group=donor.blood_group, collected_on=day, expires_on=expires,
                                     status=status)
                    s.add(unit)
                    for t in ["HIV", "HBV", "HCV", "Syphilis"]:
                        s.add(ScreeningResult(unit_id=unit.id, test=t,
                                              tested_at=datetime.combine(day, datetime.min.time()),
                                              result="positive" if (positive and t == "HBV") else "negative"))

                # hospital demand: weekday/weekend pattern + slow upward trend + noise
                for g, w in zip(GROUPS, WEIGHTS):
                    base = 14 * w * (1.25 if day.weekday() < 5 else 0.8) * (1 + 0.1 * (days - offset) / days)
                    qty = max(0, round(random.gauss(base, max(0.5, base * 0.3))))
                    if qty:
                        s.add(HospitalRequest(hospital=random.choice(HOSPITALS), bank_id=bank.id, blood_group=g,
                                              units_requested=qty,
                                              requested_at=datetime.combine(day, datetime.min.time()),
                                              status="fulfilled"))
        s.commit()
    print(f"Generated {n_units} units across {len(BANKS)} banks over {days} days.")


if __name__ == "__main__":
    main()
