from decimal import Decimal

import pytest

from packages.alpha_lifecycle.executable_reference import synthetic_next_open_accounting


def test_synthetic_reference_preserves_loss_reduced_cash_and_event_order() -> None:
    result = synthetic_next_open_accounting(
        targets=(1,0,1,0), opens=(Decimal("100"),Decimal("90"),Decimal("100"),Decimal("110")),
        boundary_times_ns=(1_000,2_000,3_000,4_000), source_days=("2026-01-01","2026-01-02","2026-01-03","2026-01-04"),
        price_increment=Decimal("0.01"),size_increment=Decimal("0.00001"),
        quote_quantum=Decimal("0.01"),minimum_notional=Decimal("10"),
    )
    fills = [row for row in result if row["kind"] == "FILL"]
    assert [row["side"] for row in fills] == ["BUY","SELL","BUY","SELL"]
    assert [row["event_time_ns"] for row in result[:4]] == [1_000,1_000,1_001,1_001]
    assert Decimal(fills[2]["quantity"]) < Decimal(fills[0]["quantity"])
    assert fills[-1]["position_after"] == "0"


def test_synthetic_reference_rejects_below_minimum_instead_of_skipping() -> None:
    with pytest.raises(ValueError,match="minimum notional"):
        synthetic_next_open_accounting(
            targets=(1,),opens=(Decimal("1000000"),),boundary_times_ns=(1,),source_days=("2026-01-01",),
            price_increment=Decimal("0.01"),size_increment=Decimal("1"),
            quote_quantum=Decimal("0.01"),minimum_notional=Decimal("10"),initial_cash=Decimal("1"),
        )



def test_synthetic_accounting_pins_rounding_at_a_tick_boundary():
    from decimal import localcontext,ROUND_CEILING,ROUND_HALF_EVEN
    with localcontext(prec=65,rounding=ROUND_CEILING):
        opening=Decimal(100)/Decimal('1.001')
    values=dict(targets=(1,0),opens=(opening,Decimal(100)),boundary_times_ns=(1,2),
        source_days=('2026-01-01','2026-01-02'),price_increment=Decimal('0.01'),
        size_increment=Decimal('0.00001'),quote_quantum=Decimal('0.01'),minimum_notional=Decimal(10))
    with localcontext(prec=50,rounding=ROUND_HALF_EVEN):
        expected=synthetic_next_open_accounting(**values)
    assert next(row['price'] for row in expected if row['kind']=='QUOTE')=='100'
    assert next(row['price'] for row in expected if row['kind']=='FILL')=='100'
    with localcontext(prec=12,rounding=ROUND_CEILING) as context:
        assert synthetic_next_open_accounting(**values)==expected
        assert (context.prec,context.rounding)==(12,ROUND_CEILING)
