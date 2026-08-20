"""A sheriff fee in MISCELLANEOUS on a Linn domestic case, 20 August.

Iowa Legal Aid reported a case whose ICOS itemization plainly reads "SHERIFFS
FEES - LOCAL" and whose workbook put the money in MISCELLANEOUS, under the note
that says the itemization could not be reconciled.

Both halves happened, and the second one caused the first. Reconciling works
category by category: a category that does not add up still has its balance
placed by the itemization, because spread_over_fee_columns reads the fee names
on its lines and puts the category total in the column they point at. That had
already put this case's balance in SHERIFF. Then a guard at the end of
reconcile_financials -- there to avoid printing five sentences about a row that
is really just the summary -- saw that no category had reconciled and handed the
whole row back. The caller fell through to summary_financials, which maps the
bucket label COSTS, and COSTS matches no fee wording, so it lands in
MISCELLANEOUS.

So the guard threw away a placement that was strictly better than the fallback
it fell back to. It now fires only when the columns really are what the summary
alone would have produced.

Every case number and amount below is synthetic. The repo is public.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs
from test_financials import FakeSheet


def _five(**totals):
    """The five bucket summary ICOS prints, everything zero by default.

    Each entry is (original, paid), defaulting paid to nothing.
    """
    rows = []
    for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER'):
        value = totals.get(label, '0')
        if isinstance(value, tuple):
            original, paid = Decimal(value[0]), Decimal(value[1])
        else:
            original, paid = Decimal(value), Decimal('0')
        rows.append({'label': label, 'original': original, 'paid': paid,
                     'due': original - paid})
    return rows


def _fee(detail, amount, paid=None):
    return {'detail': detail, 'amount': Decimal(amount), 'paid': paid}


def _case(categories, fees, total_due=None):
    case = {'id': '00000  DRCV000000', 'summary_categories': categories,
            'financials': fees, 'sentences': []}
    if total_due is not None:
        case['total_due'] = '$%s' % total_due
    return case


# The reported shape: one category carrying money, one identified fee, and an
# itemization that does not add up to what the summary says was assessed. The
# clerk assessed $100.00 of costs, the itemization names $90.00 of it as a
# sheriff fee, and $40.00 has been paid.
SHERIFF_CASE = _case(
    _five(COSTS=('100.00', '40.00')),
    [_fee('SHERIFFS FEES - LOCAL', '90.00')],
    total_due='60.00')


class TestTheReportedCase:

    def test_the_balance_is_in_the_sheriff_column(self):
        columns, _ = crs.reconcile_financials(SHERIFF_CASE)
        assert columns == {'M': Decimal('60.00')}

    def test_it_is_not_in_miscellaneous(self):
        columns, _ = crs.reconcile_financials(SHERIFF_CASE)
        assert 'O' not in columns

    def test_the_row_is_no_longer_handed_back(self):
        columns, _ = crs.reconcile_financials(SHERIFF_CASE)
        assert columns is not None

    def test_the_note_does_not_claim_a_breakdown_that_is_not_there(self):
        """There is no rest of the row: COSTS is the only category carrying."""
        _, note = crs.reconcile_financials(SHERIFF_CASE)
        assert 'rest of the row' not in note

    def test_the_note_still_says_the_category_did_not_add_up(self):
        _, note = crs.reconcile_financials(SHERIFF_CASE)
        assert 'COSTS did not add up against the itemization' in note

    def test_the_note_says_where_the_money_went(self):
        _, note = crs.reconcile_financials(SHERIFF_CASE)
        assert 'fee column its itemized lines name' in note

    def test_the_note_keeps_the_icos_total_promise(self):
        _, note = crs.reconcile_financials(SHERIFF_CASE)
        assert 'ICOS total is still right' in note

    def test_the_summary_fallback_is_what_it_used_to_get(self):
        """The behaviour being fixed, pinned so the fix cannot be undone quietly."""
        assert crs.summary_financials(SHERIFF_CASE)['O'] == Decimal('60.00')


def _written():
    sheet = FakeSheet()
    crs.process_financials(SHERIFF_CASE, sheet, 4)
    return sheet


class TestTheWorkbookRow:

    def test_the_money_is_written_to_the_sheriff_column(self):
        assert _written().value_of('M4') == Decimal('60.00')

    def test_miscellaneous_is_left_alone(self):
        assert _written().value_of('O4') is None

    def test_the_icos_total_is_still_written(self):
        assert _written().value_of('U4') == Decimal('60.00')

    def test_the_row_does_not_carry_the_summary_only_note(self):
        assert 'could not be reconciled' not in (_written().value_of('V4') or '')

    def test_the_row_does_not_say_the_fee_is_in_miscellaneous(self):
        assert 'MISCELLANEOUS' not in (_written().value_of('V4') or '')


class TestTheGuardStillFires:
    """The guard exists for a reason: a row that really is the summary.

    When the itemization cannot place the balance -- no lines, or lines whose
    fees point at different columns -- the category falls back to mapping the
    bucket's own label, which is exactly what summary_financials does. Those
    rows should still be handed back so the caller prints one sentence instead
    of several.
    """

    def test_a_category_with_no_lines_at_all_is_handed_back(self):
        case = _case(_five(COSTS=('100.00', '40.00')),
                     [_fee('SHERIFFS FEES - LOCAL', '0.00')])
        columns, note = crs.reconcile_financials(case)
        assert (columns, note) == (None, None)

    def test_an_empty_itemization_is_still_handed_back(self):
        case = _case(_five(COSTS=('100.00', '40.00')), [])
        columns, note = crs.reconcile_financials(case)
        assert (columns, note) == (None, None)

    def test_a_row_that_lands_in_miscellaneous_anyway_is_handed_back(self):
        """The fee is real, but MISC is where the summary would have put it too."""
        case = _case(_five(COSTS=('100.00', '40.00')),
                     [_fee('COPY/BINDER FEES', '90.00')])
        columns, note = crs.reconcile_financials(case)
        assert (columns, note) == (None, None)


class TestOtherFeeColumnsAreKeptToo:
    """Nothing about this is specific to the sheriff column.

    The fee has to be one the clerk files under the category that carries the
    money: a probation fee belongs to ICOS's OTHER bucket, not COSTS, so the
    summary line it has to disagree with is OTHER's.
    """

    def test_indigent_defense(self):
        case = _case(_five(COSTS=('300.00', '50.00')),
                     [_fee('INDIGENT DEFENSE FEE', '280.00')])
        columns, _ = crs.reconcile_financials(case)
        assert columns == {'J': Decimal('250.00')}

    def test_probation_out_of_the_other_bucket(self):
        case = _case(_five(OTHER=('200.00', '20.00')),
                     [_fee('PROBATION FEES', '180.00')])
        columns, _ = crs.reconcile_financials(case)
        assert columns == {'N': Decimal('180.00')}

    def test_a_category_whose_fees_name_two_columns_is_split_not_lumped(self):
        """Both fees are real, so both columns beat one lump in MISCELLANEOUS."""
        case = _case(_five(COSTS=('100.00', '40.00')),
                     [_fee('SHERIFFS FEES - LOCAL', '45.00'),
                      _fee('INDIGENT DEFENSE FEE', '45.00')])
        columns, note = crs.reconcile_financials(case)
        assert sorted(columns) == ['J', 'M']
        assert sum(columns.values()) == Decimal('60.00')
        assert 'estimates' in note

    def test_two_lines_naming_the_same_column_still_agree(self):
        case = _case(_five(COSTS=('100.00', '40.00')),
                     [_fee('SHERIFFS FEES - LOCAL', '50.00'),
                      _fee('SHERIFF SERVICE FEE', '40.00')])
        columns, _ = crs.reconcile_financials(case)
        assert columns == {'M': Decimal('60.00')}


class TestRowsThatAlreadyWorkedAreUntouched:

    def test_a_row_that_reconciles_in_full_is_unchanged(self):
        case = _case(_five(COSTS='90.00'),
                     [_fee('SHERIFFS FEES - LOCAL', '90.00')])
        columns, note = crs.reconcile_financials(case)
        assert columns == {'M': Decimal('90.00')}
        assert note is None

    def test_a_partly_reconciled_row_keeps_the_rest_of_the_row_wording(self):
        """FINE reconciles, COSTS does not, so there really is a rest of the row."""
        case = _case(_five(COSTS=('100.00', '40.00'), FINE='250.00'),
                     [_fee('SHERIFFS FEES - LOCAL', '90.00'),
                      _fee('FINE', '250.00')])
        columns, note = crs.reconcile_financials(case)
        assert columns == {'M': Decimal('60.00'), 'R': Decimal('250.00')}
        assert 'The rest of the row is fee by fee' in note
