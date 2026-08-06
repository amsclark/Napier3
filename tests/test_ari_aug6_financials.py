"""Regression coverage for Iowa Legal Aid's 6 August debt review."""

from decimal import Decimal

import crs


def _summary(**values):
    rows = []
    for label in crs.ICOS_BUCKETS:
        original, paid = values.get(label, ('0', '0'))
        original, paid = Decimal(original), Decimal(paid)
        rows.append({'label': label, 'original': original, 'paid': paid,
                     'due': original - paid})
    return rows


def _fee(detail, amount, paid='0'):
    return {'detail': detail, 'amount': Decimal(amount),
            'paid': Decimal(paid)}


def test_unexplained_costs_go_to_unknown_not_room_and_board():
    case = {
        'summary_categories': _summary(COSTS=('237.50', '17.75')),
        'financials': [_fee('ROOM/BOARD', '75.00', '16.75'),
                       _fee('FILING AND DOCKETING FEES CRIMINAL', '1.00', '1.00')],
    }
    columns, _ = crs.reconcile_financials(case)
    assert columns['L'] == Decimal('58.25')
    assert columns['P'] == Decimal('161.50')


def test_unexplained_other_balance_does_not_inflate_miscellaneous():
    case = {
        'summary_categories': _summary(OTHER=('250.00', '0')),
        'financials': [_fee('MISCELLANEOUS FEE', '200.00')],
    }
    columns, _ = crs.reconcile_financials(case)
    assert columns['O'] == Decimal('200.00')
    assert columns['P'] == Decimal('50.00')


def test_known_attorney_fee_survives_and_only_residual_is_unknown():
    case = {
        'summary_categories': _summary(COSTS=('64.00', '0')),
        'financials': [_fee('INDIGENT DEFENSE', '30.00')],
    }
    columns, _ = crs.reconcile_financials(case)
    assert columns['J'] == Decimal('30.00')
    assert columns['P'] == Decimal('34.00')


def test_summary_only_fine_goes_to_fines_not_collection_costs():
    case = {
        'summary_categories': _summary(FINE=('3100.00', '0')),
        'financials': [],
    }
    # No itemization means the summary fallback is the available truth.
    columns = crs.summary_financials(case)
    assert columns['R'] == Decimal('3100.00')
    assert 'K' not in columns


def test_partly_described_fine_uses_the_summary_fine_total():
    case = {
        'summary_categories': _summary(FINE=('3100.00', '0')),
        'financials': [_fee('DNU-FINES/FORFEITED BAIL/CIVIL PENALTY', '100.00')],
    }
    columns, _ = crs.reconcile_financials(case)
    assert columns['R'] == Decimal('3100.00')
    assert 'K' not in columns
