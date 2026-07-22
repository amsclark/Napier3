"""Court-debt reporting: what Napier reports as owed must match ICOS.

Reported July 2026: Napier showed $487.60 of collection costs on Dubuque case
FECR000000 that ICOS does not show as owed. Two separate mechanisms, both
covered here.
"""

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


class FakeSheet(dict):
    """Enough of an openpyxl worksheet to record what gets written."""

    class Cell:
        def __init__(self):
            self.value = None
            self.fill = None
            self.font = None
            self.alignment = None

    def __init__(self):
        super().__init__()
        self.cells = {}

    def __getitem__(self, key):
        return self.cells.setdefault(key, FakeSheet.Cell())

    def __setitem__(self, key, value):
        self.cells.setdefault(key, FakeSheet.Cell()).value = value

    def value_of(self, key):
        cell = self.cells.get(key)
        return cell.value if cell else None


@pytest.fixture
def felony_case():
    with open(os.path.join(FIXTURES, 'financials_felony_sample.html'), 'rb') as f:
        html = f.read()
    case = {'id': '01311 FECR000000'}
    case_parser.parse_case_financials(html, case)
    return case


def test_summary_total_due_is_the_icos_balance(felony_case):
    assert felony_case['total_due'] == "$1219.00"


def test_summary_categories_carry_payments(felony_case):
    by_label = {c['label']: c for c in felony_case['summary_categories']}
    revenue = by_label['IOWA DEPT OF REVENUE COLLECTIONS FEE']
    # The itemization shows this fee at face value with no payment; only the
    # summary records that it was paid.
    assert revenue['original'] == Decimal('182.85')
    assert revenue['paid'] == Decimal('182.85')
    assert revenue['due'] == Decimal('0.00')


def test_summary_skips_na_rows(felony_case):
    labels = [c['label'] for c in felony_case['summary_categories']]
    assert 'SUPPORT/ALIMONY' not in labels


def test_collection_costs_column_is_zero(felony_case):
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    # K is collection costs: the Linebarger fee is excluded by ICOS and the
    # revenue fee was paid, so nothing is owed here. This is the $487.60 bug.
    assert sheet.value_of('K4') in (None, Decimal('0'), Decimal('0.00'))


def test_categories_reconcile_against_icos_total(felony_case):
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert sheet.value_of('U4') == Decimal('1219.00')
    categorized = sum(v.value for k, v in sheet.cells.items()
                      if k != 'U4' and isinstance(v.value, Decimal))
    assert categorized == Decimal('1219.00')


def test_reconciled_row_is_not_flagged(felony_case):
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert sheet.cells['U4'].fill is None
    assert sheet.value_of('V4') is None


def test_third_party_fee_excluded_from_itemized_fallback(felony_case):
    # Cases where ICOS gives no per-category summary fall back to the
    # itemization, which must still leave the third-party fee out.
    del felony_case['summary_categories']
    financials = crs.itemized_financials(felony_case)
    assert financials.get('K', Decimal(0)) == Decimal('182.85')  # revenue fee only
    assert Decimal('304.75') not in financials.values()


def test_itemized_fallback_flags_the_mismatch(felony_case):
    felony_case['summary_categories'] = []
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    # Without summary categories we can't see the payment, so the row disagrees
    # with ICOS -- staff must be told to trust column U.
    assert sheet.cells['U4'].fill is not None
    assert 'trust the ICOS total' in sheet.value_of('V4')


@pytest.mark.parametrize("text,expected", [
    ("$1,401.85", Decimal('1401.85')),
    ("0.00", Decimal('0')),
    ("(25.00)", Decimal('-25.00')),
    ("N/A", None),
    ("", None),
    (None, None),
    ("SHERIFFS FEES", None),
])
def test_parse_money(text, expected):
    assert case_parser.parse_money(text) == expected
