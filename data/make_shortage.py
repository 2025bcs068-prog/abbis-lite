"""Stage a demo shortage: drain one bank's stock of a blood group, so the forecast flags a shortfall."""
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BloodBank, BloodUnit


def main(bank_name="Mbarara", group="A+", keep=5):
    with SessionLocal() as s:
        bank = s.scalar(select(BloodBank).where(BloodBank.name.like(f"%{bank_name}%")))
        units = s.scalars(
            select(BloodUnit).where(BloodUnit.bank_id == bank.id, BloodUnit.blood_group == group,
                                    BloodUnit.status == "available", BloodUnit.expires_on >= date.today())
        ).all()
        for u in units[keep:]:
            u.status = "issued"
        s.commit()
        print(f"{bank.name}: {group} stock {len(units)} -> {min(keep, len(units))}")


if __name__ == "__main__":
    main()
