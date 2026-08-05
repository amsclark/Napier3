"""Collection fees the clerk's own summary leaves out of the balance.

The county attorney's collection fee usually counts toward the case balance
under FINE. On two of the 400 captured cases the clerk's arithmetic says
otherwise. One case counts its state and county collection splits and leaves
the bare COLLECTION BY CO ATTY fee out, so the itemization runs over the
summary by exactly that fee. Another lists every collection fee plus a second
ledger entry for an already-paid surcharge, identical wording and amount but
no payment, no receipt, no date, and counts none of them; the raw ICOS page
itself carries those rows, so they are superseded ledger entries, not a
parsing error and not debt.

uncounted_collection_rows decides per case, the way summary_counts_third_party
already does for third party fees, and only on the clerk's own arithmetic: a
group of rows is excluded only when the itemization exceeds the summary by
exactly that group's sum, every bucket then reconciles to the cent, and no
other grouping manages the same.

Every case number and amount below is synthetic. The repo is public.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs


def _five(**totals):
    rows = []
    for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER'):
        original = Decimal(totals.get(label, '0'))
        paid = Decimal(totals.get('%s_PAID' % label, '0'))
        rows.append({'label': label, 'original': original, 'paid': paid,
                     'due': original - paid})
    return rows


def _fee(detail, amount, paid=None):
    return {'detail': detail,
            'amount': Decimal(amount) if amount is not None else None,
            'paid': Decimal(paid) if paid is not None else None}


# The first shape: the summary's FINE is the two collection splits and nothing
# else. The bare collection fee is listed, partially paid, and not counted.
# Before the fix the FINE bucket ran $60.00 over, failed, and surrendered its
# breakdown with a note; the $60.00 was never owed by the clerk's own total.
SPLIT_CASE = {
    'id': '00000  FECR000000',
    'summary_categories': _five(COSTS='90.00', FINE='450.00',
                                FINE_PAID='45.00'),
    'financials': [
        _fee('SHERIFF SERVICE FEES', '90.00'),
        _fee('COLLECTION BY CO ATTY - STATE SPLIT', '300.00', '30.00'),
        _fee('COLLECTION BY CO ATTY - COUNTY SPLIT', '150.00', '15.00'),
        _fee('COLLECTION BY CO ATTY', '60.00', '12.00'),
    ],
}


def test_a_collection_fee_the_summary_leaves_out_is_excluded():
    assert crs.uncounted_collection_rows(SPLIT_CASE) == frozenset({3})


def test_the_split_case_reconciles_fee_by_fee():
    columns, note = crs.reconcile_financials(SPLIT_CASE)
    assert note is None, note
    assert columns is not None
    # FINE due 405.00 stays with the two split lines; COSTS 90.00 in SHERIFF.
    assert sum(columns.values()) == Decimal('495.00'), columns
    assert columns.get('M') == Decimal('90.00'), columns


# The second shape: a fully paid case whose page lists a second ledger entry
# for the paid surcharge (same wording and amount, no payment) and collection
# fees the summary counts nowhere. Before the fix both FINE and SURCHARGE
# failed the partition and the row said two categories could not be broken
# down, on a case the clerk shows as paid off to the cent.
SUPERSEDED_CASE = {
    'id': '00000  AGCR000000',
    'summary_categories': _five(FINE='700.00', FINE_PAID='700.00',
                                SURCHARGE='105.00', SURCHARGE_PAID='105.00'),
    'financials': [
        _fee('CRIME SERVICES SURCHARGE', '105.00', '105.00'),
        _fee('CRIME SERVICES SURCHARGE', '105.00'),
        _fee('COLLECTION BY CO ATTY - COUNTY SPLIT', '80.00', '80.00'),
        _fee('COLLECTION BY CO ATTY', '250.00'),
        _fee('STATE FINES/COUNTY SPEED,WEIGHT,COPYCAT ORDINANCES',
             '700.00', '540.00'),
        _fee('COLLECTION BY CO ATTY(THRESHOLD MET)-CO SPLIT',
             '70.00', '70.00'),
        _fee('COLLECTION BY CO ATTY(THRESHOLD MET)-CO ATTY',
             '10.00', '10.00'),
    ],
}


def test_superseded_ledger_entries_are_excluded():
    skip = crs.uncounted_collection_rows(SUPERSEDED_CASE)
    assert skip == frozenset({1, 2, 3, 5, 6})


def test_the_superseded_case_reconciles_with_nothing_owed():
    columns, note = crs.reconcile_financials(SUPERSEDED_CASE)
    assert note is None, note
    assert columns == {}, columns


# The guard: a case where the overage equals the collection fees to the cent
# and excluding them would still be wrong. Here the summary counts the
# collection fee under FINE and the real discrepancy is in OTHER, so pulling
# the collection fee out would break FINE to fix nothing. The rule must not
# fire, and the honest failure note must survive.
CARVE_OUT_CASE = {
    'id': '00000  SRCR000000',
    'summary_categories': _five(FINE='85.00', OTHER='715.00'),
    'financials': [
        _fee('DELINQUENT PMT TO REVOLVING FUND', '800.00'),
        _fee('COLLECTION BY CO ATTY', '85.00', '85.00'),
    ],
}


def test_the_rule_refuses_when_exclusion_would_break_another_bucket():
    assert crs.uncounted_collection_rows(CARVE_OUT_CASE) == frozenset()
    columns, note = crs.reconcile_financials(CARVE_OUT_CASE)
    assert note is not None and 'OTHER' in note, note


# A counted collection fee, which is the common shape: totals agree, nothing
# to exclude, and the fee keeps its place in the FINE bucket.
COUNTED_CASE = {
    'id': '00000  SMSM000000',
    'summary_categories': _five(FINE='40.00'),
    'financials': [
        _fee('COLLECTION BY CO ATTY', '40.00'),
    ],
}


def test_a_counted_collection_fee_is_left_alone():
    assert crs.uncounted_collection_rows(COUNTED_CASE) == frozenset()
    columns, note = crs.reconcile_financials(COUNTED_CASE)
    assert note is None, note
    assert sum(columns.values()) == Decimal('40.00'), columns


def test_an_ordinary_unpaid_duplicate_is_not_touched():
    """Two identical unpaid assessments the summary counts, as two counts of
    the same charge legitimately produce. The totals agree, so the duplicate
    group must never form an exclusion."""
    case = {
        'id': '00000  SCSC000000',
        'summary_categories': _five(SURCHARGE='250.00'),
        'financials': [
            _fee('CRIME SERVICES SURCHARGE', '125.00'),
            _fee('CRIME SERVICES SURCHARGE', '125.00'),
        ],
    }
    assert crs.uncounted_collection_rows(case) == frozenset()
    columns, note = crs.reconcile_financials(case)
    assert note is None, note
    assert columns == {'Q': Decimal('250.00')}, columns
