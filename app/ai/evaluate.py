"""Print backtest accuracy per bank and blood group. Run: python -m app.ai.evaluate"""
from sqlalchemy import select

from app.ai.forecast import MODELS, backtest, demand_series
from app.db import SessionLocal
from app.models import BloodBank

with SessionLocal() as db:
    print(f"{'bank':32}{'group':6}" + "".join(f"{m:>16}" for m in MODELS) + "   (WAPE, lower is better)")
    for bank in db.scalars(select(BloodBank)).all():
        for g in ["O+", "A+", "B+"]:
            metrics, _ = backtest(demand_series(db, bank.id, g))
            print(f"{bank.name:32}{g:6}" + "".join(f"{metrics[m]['wape']:>16.3f}" for m in MODELS))
