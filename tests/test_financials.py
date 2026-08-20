"""Court-debt reporting: what Napier reports as owed must match ICOS.

Reported July 2026: Napier showed $487.60 of collection costs on a Dubuque
felony case that ICOS does not show as owed. Two separate mechanisms, both
covered here. The fixture is that real page with the case and the defendant
scrubbed out, because this repo is public.
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


def test_summary_is_a_rollup_not_a_fee_breakdown(felony_case):
    # ICOS summarises into five fixed buckets. It does not break the balance
    # out by fee type, so there is no per-fee row to read a payment off.
    labels = [c['label'] for c in felony_case['summary_categories']]
    assert labels == ['COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER']


def test_summary_categories_carry_payments(felony_case):
    by_label = {c['label']: c for c in felony_case['summary_categories']}
    # The revenue fee lands in OTHER. The itemization shows it at face value
    # with the Paid column blank; only the summary records that it was paid.
    other = by_label['OTHER']
    assert other['original'] == Decimal('272.85')
    assert other['paid'] == Decimal('182.85')
    assert other['due'] == Decimal('90.00')


def test_summary_skips_na_rows(felony_case):
    labels = [c['label'] for c in felony_case['summary_categories']]
    assert 'SUPPORT/ALIMONY' not in labels


def test_collection_costs_column_is_zero(felony_case):
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    # K is collection costs: the Linebarger fee is excluded by ICOS and the
    # revenue fee was paid, so nothing is owed here. This is the $487.60 bug.
    # On the summary path K is never written at all, because the summary has no
    # collection-fee bucket to map from -- see the MISC test below.
    assert sheet.value_of('K4') in (None, Decimal('0'), Decimal('0.00'))


def test_the_written_row_keeps_the_fee_detail(felony_case):
    """What the summary path used to cost, and no longer does.

    Preferring the summary reconciled against the ICOS balance, which is what
    the reported bug was about, but its five buckets carry no fee detail: COSTS
    and OTHER both fell through to MISC and the columns the CRS workbook exists
    to fill came back empty. Reconciling gets both, so the row now carries the
    fees by name and still totals to what ICOS says is owed.
    """
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert sheet.value_of('M4') == Decimal('579.00')   # sheriff fees
    assert sheet.value_of('J4') == Decimal('50.00')    # indigent defense
    assert sheet.value_of('P4') == Decimal('90.00')    # revolving fund
    assert sheet.value_of('S4') == Decimal('500.00')   # restitution
    assert sheet.value_of('O4') is None                # nothing left to dump in MISC
    assert sheet.value_of('U4') == Decimal('1219.00')


def test_one_broken_category_does_not_cost_the_others(felony_case):
    """A partition that does not add up must fall back, and no further.

    Moving a fee off its assessed amount breaks the bucket check inside
    `reconcile_financials`, which is the safety net: that bucket's split is
    given up rather than written wrong. It used to cost the whole row, and the
    line item moved here is a revolving fund fee, so the sheriff and indigent
    defense fees in a different bucket were being emptied into MISCELLANEOUS
    over a discrepancy that had nothing to do with them. J, K and L going empty
    is what the statute of limitations and Polk sheets read as no debt to chase.
    """
    felony_case['financials'][0]['amount'] = '31.00'   # an OTHER line
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    # The category total is ICOS's, because the itemization disagreed with it.
    # Which column it belongs in is still the itemization's to say, and every
    # fee in this bucket is a revolving fund fee, so it is column P and not
    # MISCELLANEOUS. Iowa Legal Aid asked for this in August 2026 after a
    # workbook moved a $60 attorney fee out of INDIGENT DEFENSE over a
    # discrepancy in a different category.
    assert sheet.value_of('P4') == Decimal('90.00')    # OTHER, as a total
    assert sheet.value_of('O4') is None
    assert sheet.value_of('M4') == Decimal('579.00')   # sheriff fees survive
    assert sheet.value_of('J4') == Decimal('50.00')    # indigent defense too
    assert sheet.value_of('S4') == Decimal('500.00')
    assert sheet.value_of('U4') == Decimal('1219.00')
    assert 'OTHER' in sheet.value_of('V4')
    assert 'category total' in sheet.value_of('V4')


def test_a_collapsed_row_says_it_is_collapsed(felony_case):
    """An empty sheriff column has two meanings and staff cannot see which.

    Folded into MISCELLANEOUS looks exactly like never charged. The row that
    cannot be reconciled has to say so on its face, since nothing else about it
    is different.

    The word MISCELLANEOUS is no longer what says it, because the balance no
    longer goes there when the itemization can name a better column. What has
    to survive is the row admitting that this category came off the summary.
    """
    felony_case['financials'][0]['amount'] = '31.00'
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    note = sheet.value_of('V4')
    assert 'OTHER did not add up' in note
    assert 'category total' in note
    assert 'ICOS total is still right' in note


def test_a_paid_off_case_does_not_warn_about_a_breakdown_of_nothing(felony_case):
    """ICOS says $0.00 due, so every fee column is zero however it got there.

    The caveat explains that sheriff and indigent defense debt is hiding inside
    MISCELLANEOUS, and on this row there is no debt anywhere to hide. Of the 25
    captured pages that carried one of these, 23 owed nothing. Column V is also
    where the notes staff actually have to read go, the disposition Napier had
    to guess at and the case Iowa Courts would not give up, so filling it with
    warnings about paid off cases teaches people to skip the column.
    """
    felony_case['total_due'] = '$0.00'
    felony_case['summary_categories'] = _five_buckets(
        COSTS=(Decimal('100.00'), Decimal('100.00')))
    felony_case['financials'] = [
        {'detail': 'SHERIFFS FEES - LOCAL', 'amount': Decimal('50.00'),
         'paid': None},
    ]
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert sheet.value_of('U4') == Decimal('0.00')
    assert sheet.value_of('V4') is None


def test_the_same_collapsed_row_still_warns_when_money_is_owed(felony_case):
    """What silences the caveat is the balance, not the fallback that wrote it.

    Same page, same failure to reconcile, one hundred dollars outstanding. If
    this row goes quiet too then the change has thrown the warning away rather
    than aimed it.
    """
    felony_case['total_due'] = '$100.00'
    felony_case['summary_categories'] = _five_buckets(
        COSTS=(Decimal('100.00'), Decimal('0')))
    felony_case['financials'] = [
        {'detail': 'SHERIFFS FEES - LOCAL', 'amount': Decimal('50.00'),
         'paid': None},
    ]
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert 'UNKNOWN' in sheet.value_of('V4')


def test_a_zero_balance_does_not_silence_a_real_disagreement(felony_case):
    """Fee columns adding up to money against a $0.00 balance is worth saying.

    Only the caveat about where the columns came from goes quiet on a paid off
    case. This note is not that. It is ICOS and the sheet contradicting each
    other, which is the one thing on this row a staffer has to be told.
    """
    felony_case['summary_categories'] = []
    felony_case['total_due'] = '$0.00'
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert sheet.cells['U4'].fill is not None
    assert 'trust the ICOS total' in sheet.value_of('V4')
    assert 'assessed rather than owed' not in sheet.value_of('V4')


def test_the_itemized_fallback_says_the_figures_are_assessed(felony_case):
    del felony_case['summary_categories']
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    note = sheet.value_of('V4')
    assert 'no category summary' in note
    assert 'assessed rather than owed' in note


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


def test_reconciled_keeps_the_breakdown_and_matches_icos(felony_case):
    """The whole point: fee detail and the ICOS balance at the same time.

    The summary path gets the total right and loses the fees. The itemized path
    keeps the fees and overstates the total by whatever was already paid.
    Partitioning the itemization into the summary's buckets and subtracting each
    bucket's payment from the lines that account for it gets both.
    """
    columns, note = crs.reconcile_financials(felony_case)
    assert note is None
    assert columns == {
        'M': Decimal('579.00'),   # two sheriff fees, nothing paid
        'J': Decimal('50.00'),    # indigent defense
        'P': Decimal('90.00'),    # revolving fund, five lines
        'S': Decimal('500.00'),   # restitution
    }
    assert sum(columns.values()) == Decimal('1219.00')
    assert 'K' not in columns   # the revenue fee was paid; this was the bug


def test_reconciled_total_equals_the_icos_total(felony_case):
    columns, _ = crs.reconcile_financials(felony_case)
    total_due = Decimal(felony_case['total_due'].replace('$', ''))
    assert sum(columns.values()) == total_due


def test_reconciliation_needs_both_halves(felony_case):
    del felony_case['summary_categories']
    assert crs.reconcile_financials(felony_case) == (None, None)


def test_reconciliation_refuses_a_partition_that_does_not_add_up(felony_case):
    # If a fee lands in the wrong bucket the bucket stops matching its summary
    # original. That must cost us the per-fee split inside that bucket, not the
    # knowledge of which columns the bucket's fees belong to. Here every fee in
    # the broken bucket is a revolving fund fee, so there is one column and the
    # answer is exact even though the split is given up.
    felony_case['financials'][0]['amount'] = '31.00'
    columns, note = crs.reconcile_financials(felony_case)
    assert columns == {
        'M': Decimal('579.00'),
        'J': Decimal('50.00'),
        'P': Decimal('90.00'),    # OTHER, off the summary rather than by fee
        'S': Decimal('500.00'),
    }
    assert 'O' not in columns
    assert 'OTHER' in note


def test_a_row_where_nothing_reconciles_still_keeps_the_fee_columns(felony_case):
    # No bucket adds up, but the itemization still names the fees, and a fee
    # column the page states beats a lump in MISCELLANEOUS. This used to fall
    # back to the summary whole and put $719.00 in MISC under a note blaming
    # the itemization -- which is how a plainly labelled sheriff fee ended up
    # filed as miscellaneous on a Linn case in August.
    for row in felony_case['financials']:
        if row['amount'] is not None:
            row['amount'] = str(Decimal(row['amount']) + 1)
    columns, note = crs.reconcile_financials(felony_case)
    assert columns is not None
    sheet = FakeSheet()
    crs.process_financials(felony_case, sheet, 4)
    assert sheet.value_of('M4') == Decimal('578.24')   # sheriff fees
    assert sheet.value_of('J4') == Decimal('50.76')    # indigent defense
    assert sheet.value_of('S4') == Decimal('500.00')   # restitution
    assert sheet.value_of('O4') is None                # not lumped into MISC
    assert sheet.value_of('U4') == Decimal('1219.00')  # ICOS balance unchanged
    # The row still says the categories did not add up, and no longer promises
    # a fee-by-fee breakdown of a row that has none.
    assert 'did not add up against the itemization' in sheet.value_of('V4')
    assert 'rest of the row' not in sheet.value_of('V4')


def test_ambiguous_payment_is_flagged_not_guessed():
    # Two lines of the same amount in one bucket, one of them paid. Nothing on
    # the page says which, so the balance is apportioned across the two columns
    # those fees belong to and the row says the split is an estimate. It used to
    # go to MISCELLANEOUS whole, which reads as neither fee having been charged.
    case = {
        'total_due': '$25.00',
        'summary_categories': [
            {'label': 'COSTS', 'original': Decimal('0'), 'paid': Decimal('0'),
             'due': Decimal('0')},
            {'label': 'FINE', 'original': Decimal('0'), 'paid': Decimal('0'),
             'due': Decimal('0')},
            {'label': 'SURCHARGE', 'original': Decimal('0'),
             'paid': Decimal('0'), 'due': Decimal('0')},
            {'label': 'RESTITUTION', 'original': Decimal('0'),
             'paid': Decimal('0'), 'due': Decimal('0')},
            {'label': 'OTHER', 'original': Decimal('50.00'),
             'paid': Decimal('25.00'), 'due': Decimal('25.00')},
        ],
        'financials': [
            {'detail': 'DELINQUENT REVOLVING FUND OBLIGATION',
             'amount': '25.00', 'paid': None, 'paidDate': None},
            {'detail': 'IOWA DEPT OF REVENUE COLLECTIONS FEE',
             'amount': '25.00', 'paid': None, 'paidDate': None},
        ],
    }
    columns, note = crs.reconcile_financials(case)
    assert columns == {'P': Decimal('12.50'), 'K': Decimal('12.50')}
    assert sum(columns.values()) == Decimal('25.00')
    assert note is not None and 'OTHER' in note
    assert 'estimates' in note


def test_third_party_fee_stays_out_of_the_partition(felony_case):
    # ICOS itemises the Linebarger fee but leaves it out of the totals, so it
    # must not be counted when checking a bucket against its summary original.
    details = [f['detail'] for f in felony_case['financials']]
    assert any('THIRD PARTY' in (d or '') for d in details)
    columns, note = crs.reconcile_financials(felony_case)
    assert columns is not None
    assert Decimal('304.75') not in columns.values()


def _third_party_case(other_original, other_paid):
    """A case owing one ordinary OTHER fee and one third party collection fee.

    other_original is what the ICOS summary says the OTHER bucket was assessed.
    Set it to 40 and the summary has left the collection fee out, which is the
    common shape. Set it to 100 and the summary has counted it.
    """
    def zero(label):
        return {'label': label, 'original': Decimal('0'),
                'paid': Decimal('0'), 'due': Decimal('0')}

    original = Decimal(other_original)
    paid = Decimal(other_paid)
    return {
        'total_due': '$%s' % (original - paid),
        'summary_categories': [
            zero('COSTS'), zero('FINE'), zero('SURCHARGE'), zero('RESTITUTION'),
            {'label': 'OTHER', 'original': original, 'paid': paid,
             'due': original - paid},
        ],
        'financials': [
            {'detail': 'DELINQUENT REVOLVING FUND OBLIGATION',
             'amount': '40.00', 'paid': None, 'paidDate': None},
            {'detail': 'THIRD PARTY DEBT COLLECTION FEE',
             'amount': '60.00', 'paid': None, 'paidDate': None},
        ],
    }


def test_a_summary_that_leaves_the_third_party_fee_out_still_drops_it():
    """The five captured cases of this shape, and the reason the rule exists.

    The itemisation assesses 100 and the summary only 40, which is ICOS saying
    the collection agency's 60 is not part of what it will collect. Column K
    stays empty and the row reports the 40 ICOS stands behind.
    """
    columns, _ = crs.reconcile_financials(_third_party_case('40.00', '0'))
    assert columns == {'P': Decimal('40.00')}


def test_a_summary_that_counts_the_third_party_fee_keeps_it_in_column_k():
    """The other four captured cases.

    Here the itemisation and the summary both say 100, so ICOS did count the
    collection fee. Dropping it would leave the bucket 60 short of its own
    summary, and the whole balance would come back as a category total in
    column P instead of splitting into the two fees that make it up.

    Column K is one of the two columns Iowa Legal Aid treats as surely
    dischargeable, so losing 60 out of it is not cosmetic.
    """
    columns, note = crs.reconcile_financials(_third_party_case('100.00', '0'))
    assert columns == {'P': Decimal('40.00'), 'K': Decimal('60.00')}
    assert note is None


def test_the_check_is_the_summary_total_not_the_wording():
    crs_case = _third_party_case('100.00', '0')
    assert crs.summary_counts_third_party(crs_case) is True
    assert crs.summary_counts_third_party(_third_party_case('40.00', '0')) is False


def test_a_case_with_no_third_party_fee_is_not_affected():
    """The check only ever answers a question about a fee that is there."""
    case = _third_party_case('40.00', '0')
    case['financials'] = [case['financials'][0]]
    assert crs.summary_counts_third_party(case) is False


def test_a_counted_third_party_fee_that_was_paid_off_owes_nothing():
    """All four captured cases of this shape are paid in full.

    The bucket reconciles, the payment is attributed to the lines that account
    for it, and nothing is owed. This is the corpus today, and it is why the
    change above moves no captured row.
    """
    columns, _ = crs.reconcile_financials(_third_party_case('100.00', '100.00'))
    assert not columns or sum(columns.values()) == Decimal('0')


@pytest.mark.parametrize("detail,bucket", [
    ("SHERIFFS FEES - LOCAL", "COSTS"),
    ("INDIGENT DEFENSE-FELONY-REIMBURSE STATE", "COSTS"),
    ("RESTITUTIONS", "RESTITUTION"),
    ("CRIMINAL PENALTY SURCHARGE", "SURCHARGE"),
    ("FINE", "FINE"),
    # Fine wording, filed under COSTS by the clerk. Measured across 259
    # captured cases carrying both halves: moving it gained two buckets and
    # cost none, and there is no case in the corpus where the summary counts it
    # as a fine. Thin evidence, so it is worth revisiting, but it is one sided,
    # and this decides only the reconciliation. The column the client reads is
    # still FINES.
    ("NONSCHEDULED CHAPTER 321", "COSTS"),
    ("SCHEDULED VIOLATION/NON-SCHEDULED", "COSTS"),
    # The county attorney's collection fee is charged against the fine and the
    # summary counts it there. Six buckets and three whole rows, no losses.
    ("COLLECTION BY CO ATTY (THRESHOLD MET)", "FINE"),
    # Filed under COSTS despite the wording. Probation revocation was the
    # single biggest cause of a broken partition in the corpus, at thirteen
    # buckets, and it broke the bucket that holds indigent defense fees.
    ("PROBATION REVOCATION FEE", "COSTS"),
    ("PARKING VIOLATION PER COMPLAINT", "COSTS"),
    ("REFUNDABLES DUE TO PREPAID EXPENSES", "COSTS"),
    ("DNU-DELINQUENT REVOLVING FUND OBLIGATION", "OTHER"),
    ("IOWA DEPT OF REVENUE COLLECTIONS FEE", "OTHER"),
    ("", "OTHER"),
    (None, "OTHER"),
    # Both of these read like OTHER. ICOS's own summary says otherwise, on
    # pages where every other fee matched to the cent, so the only thing
    # keeping those two rows from reconciling was this classification.
    ("POSTAGE FEES", "COSTS"),
    ("MISC FEES BY CITY/COUNTY", "COSTS"),
])
def test_summary_bucket_classification(detail, bucket):
    assert crs.get_summary_bucket(detail) == bucket


def test_a_misc_sounding_fee_does_not_cost_the_row_its_breakdown():
    """The shape of a real Polk County page, with the amounts it carried.

    ICOS put all four of these in COSTS and reported 227.00 assessed. Napier
    read the city/county line as OTHER, so COSTS came up 37.00 short and OTHER
    37.00 over, both failed the partition check, and every fee on the row
    collapsed into one category total. Columns J and M are where the
    statute-of-limitations and Polk room-and-board sheets look for indigent
    defense and jail debt, and they came out empty on a case that had both.
    """
    case = {
        'summary_categories': _five_buckets(
            COSTS=(Decimal('227.00'), Decimal('0'))),
        'financials': [
            {'detail': 'FILING AND DOCKETING FEES CRIMINAL',
             'amount': Decimal('100.00'), 'paid': None},
            {'detail': 'SHERIFFS FEES - LOCAL',
             'amount': Decimal('30.00'), 'paid': None},
            {'detail': 'MISC FEES BY CITY/COUNTY',
             'amount': Decimal('37.00'), 'paid': None},
            {'detail': 'INDIGENT DEFENSE-MISDM-REIMBURSE STATE',
             'amount': Decimal('60.00'), 'paid': None},
        ],
    }
    columns, note = crs.reconcile_financials(case)
    assert columns is not None, 'the row fell back to category totals'
    assert note is None
    assert columns['J'] == Decimal('60.00'), 'indigent defense'
    assert columns['M'] == Decimal('30.00'), 'sheriff'
    # Still lands on the balance ICOS reports, which is the check that says the
    # breakdown was not bought by inventing money.
    assert sum(columns.values()) == Decimal('227.00')


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


def _five_buckets(**due):
    """The five-bucket summary ICOS prints, with everything zero by default."""
    rows = []
    for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER'):
        original, paid = due.get(label, (Decimal('0'), Decimal('0')))
        rows.append({'label': label, 'original': original, 'paid': paid,
                     'due': original - paid})
    return rows


def test_instalment_payments_are_credited_to_the_line_they_paid():
    """Restitution is paid down in instalments and ICOS lists each one.

    The instalments arrive as continuation rows: no detail, no amount, just a
    payment against the line above. Those rows were being skipped, so a bucket
    whose payments were sitting right there on the page looked unattributable,
    and a restitution balance went to MISC. Found by running seventy real
    captured cases through this function; it moved $3,227.97 of restitution
    into MISC across thirty-six of them.
    """
    case = {
        'total_due': '$1108.20',
        'summary_categories': _five_buckets(
            RESTITUTION=(Decimal('1200.00'), Decimal('91.80'))),
        'financials': [
            {'detail': 'RESTITUTIONS', 'amount': '1200.00', 'paid': '28.17'},
            {'detail': None, 'amount': None, 'paid': '4.36'},
            {'detail': None, 'amount': None, 'paid': '9.60'},
            {'detail': None, 'amount': None, 'paid': '15.37'},
            {'detail': None, 'amount': None, 'paid': '10.18'},
            {'detail': None, 'amount': None, 'paid': '10.83'},
            {'detail': None, 'amount': None, 'paid': '0.10'},
            {'detail': None, 'amount': None, 'paid': '13.19'},
        ],
    }
    columns, note = crs.reconcile_financials(case)
    assert note is None, 'the payments are on the page; nothing to flag'
    assert columns == {'S': Decimal('1108.20')}


def test_an_unattributable_payment_keeps_its_category():
    """A bucket we cannot split by line is still a bucket we can name.

    Two restitution lines and a payment that could have been either. Which fee
    was paid is unknowable, but that the balance is restitution is not, and
    restitution is the one distinction the spreadsheet cannot afford to lose:
    it drives expungement and 910.7 eligibility, not just the debt total.
    """
    case = {
        'total_due': '$75.00',
        'summary_categories': _five_buckets(
            RESTITUTION=(Decimal('100.00'), Decimal('25.00'))),
        'financials': [
            {'detail': 'RESTITUTIONS', 'amount': '50.00', 'paid': None},
            {'detail': 'RESTITUTIONS', 'amount': '50.00', 'paid': None},
        ],
    }
    columns, note = crs.reconcile_financials(case)
    assert columns == {'S': Decimal('75.00')}, 'not MISC'
    assert note is not None and 'RESTITUTION' in note


def test_old_fee_debt_survives_a_broken_category_elsewhere():
    """The twenty year old debt Iowa Legal Aid is actually litigating.

    Attorney fees land in J and K and jail and room and board lands in L, and
    the SOL and Polk sheets read those three columns and nothing else. A stray
    revolving fund fee in a different summary category used to empty all three
    into MISCELLANEOUS, and a case sitting in MISCELLANEOUS reads on those
    sheets as a case with nothing to chase.
    """
    case = {
        'total_due': '$425.00',
        'summary_categories': _five_buckets(
            COSTS=(Decimal('400.00'), Decimal('0')),
            OTHER=(Decimal('50.00'), Decimal('25.00'))),
        'financials': [
            {'detail': 'INDIGENT DEFENSE-FELONY-REIMBURSE STATE',
             'amount': '150.00', 'paid': None},
            {'detail': 'JAIL FEES-ROOM/BOARD', 'amount': '250.00',
             'paid': None},
            {'detail': 'DELINQUENT REVOLVING FUND OBLIGATION',
             'amount': '30.00', 'paid': None},
        ],
    }
    columns, note = crs.reconcile_financials(case)
    assert columns['J'] == Decimal('150.00')
    assert columns['L'] == Decimal('250.00')
    assert columns['P'] == Decimal('25.00')   # the revolving fund's own column
    assert 'O' not in columns
    assert 'OTHER' in note


def test_the_wording_icos_actually_uses_for_room_and_board_reaches_column_l():
    """The one above uses `JAIL FEES-ROOM/BOARD`, which ICOS does not write.

    What it writes is `REIMBURSE-SHERIFF-ROOM/BOARD/MEDICAL`, on 11 lines worth
    $18,907.56 across the 300 captured cases, and that wording carries the word
    SHERIFF as well. get_finance_column tests ROOM/BOARD first and sheriff fees
    are column M, so the order of those two branches is the only thing keeping
    this out of the sheriff column. Nothing said so, and a wording invented for
    a fixture cannot say it, because it does not contain the word.

    Getting it wrong empties the POLK R&B APPEAL sheet and the SOL sheet's
    column D, which are the two places an attorney goes looking for room and
    board to argue about.
    """
    assert crs.get_finance_column('REIMBURSE-SHERIFF-ROOM/BOARD/MEDICAL') == 'L'
    assert crs.get_finance_column('SHERIFFS FEES - LOCAL') == 'M'


def test_an_unattributable_payment_is_apportioned_not_dumped():
    """What happens when the lines in an unsplittable bucket disagree.

    This used to be the case that went to MISCELLANEOUS whole, on the reasoning
    that no single column was honest. Iowa Legal Aid's answer in August 2026 was
    that MISCELLANEOUS is the least honest column of the lot: the bankruptcy
    sheet treats J and K as surely dischargeable and everything else as maybe,
    and the 910.7 sheet stops reading at P, so a fee that arrives in
    MISCELLANEOUS is a fee no sheet can reason about.

    So the balance is split by what the fees were assessed at, and the row says
    the split is an estimate. Half of a $50 bucket was paid, and each of the two
    $25 fees carries half the remainder.
    """
    case = {
        'total_due': '$25.00',
        'summary_categories': _five_buckets(
            OTHER=(Decimal('50.00'), Decimal('25.00'))),
        'financials': [
            {'detail': 'DELINQUENT REVOLVING FUND OBLIGATION',
             'amount': '25.00', 'paid': None},
            {'detail': 'IOWA DEPT OF REVENUE COLLECTIONS FEE',
             'amount': '25.00', 'paid': None},
        ],
    }
    columns, note = crs.reconcile_financials(case)
    assert columns == {'P': Decimal('12.50'), 'K': Decimal('12.50')}
    assert note is not None and 'estimates' in note
