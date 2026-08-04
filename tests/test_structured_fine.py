"""Fees read by how they are collected instead of by what they are.

A structured fine is Iowa's instalment arrangement for court debt. A clerk
itemizing one writes each component fee with the arrangement's name attached,
so the itemization carries lines like "INDIGENT DEFENSE-STRUC FINES-REIMB
STATE" and "TIME PAYMENT FEES-STRUCTURED FINES". The trailing words say how the
money is being collected. The leading words say what it is. Napier tested for
the word FINE anywhere in the wording, before it tested for anything more
specific, so all four component fees were read as fines.

Two things went wrong at once, and the second is the one that gives the first
away.

The clerk does not agree. On two Polk cases in the 400 case corpus the
itemization ran $79.00 over the summary's FINE total and $79.00 under its COSTS
total, and $79.00 is exactly these four fees, twice, to the cent. That
shortfall-and-matching-excess is the signature reconcile_financials exists to
catch, so both categories failed their partition check and both rows told the
staffer their balances could not be broken down fee by fee. Only the fine
itself, FINES AND FORFEITED BAIL-STRUCTURED FINES, matched the summary's FINE
on the nose.

And within a category that does reconcile, the reimbursement to the state
public defender came out in FINES rather than INDIGENT DEFENSE. The
expungement sheet reads columns J through P and cannot see FINES at all, the
bankruptcy sheet calls FINES not dischargeable, and the statute of limitations
sheet looks for old attorney fee debt in J and K. So $30.00 of indigent defense
debt was invisible to three sheets and wrongly counted by a fourth.

The same two letters cost a victim surcharge its category. ICOS abbreviates the
word when the wording runs long, and "DOMESTIC/SEXUAL ABUSE, STALKING, HUMAN
TRAFF VICTIM SURCH" runs long. Napier tested for SURCHARGE, missed it, and a
$100.00 surcharge landed in OTHER, which left the SURCHARGE bucket short by
exactly $100.00 and OTHER over by exactly $100.00 on a third Polk case, failing
both.

Measured across the corpus, the fix clears three false "did not add up"
warnings and takes one case from a category total to a real fee breakdown. It
moves no money there, because those three rows are either paid off or land in
the same column either way. The money in the first test below is what happens
when they do not.

Every case number and amount below is synthetic. The repo is public.
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs


def _five(**totals):
    """The five bucket summary ICOS prints, everything zero by default."""
    rows = []
    for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER'):
        original = Decimal(totals.get(label, '0'))
        rows.append({'label': label, 'original': original,
                     'paid': Decimal('0'), 'due': original})
    return rows


def _fee(detail, amount):
    return {'detail': detail, 'amount': Decimal(amount), 'paid': None}


# The shape of the two Polk structured fine cases, with the balance still owed
# rather than paid off, which is the only difference that matters here.
STRUCTURED_FINE = {
    'id': '00000  SRCR000000',
    'summary_categories': _five(COSTS='85.00', FINE='232.00',
                                SURCHARGE='69.60'),
    'financials': [
        _fee('DNU-DOCKET PROC - STRUCT FINE ABOVE SIMP', '30.00'),
        _fee('DNU-COURT REPORTER SERVICES STRUC FINE', '15.00'),
        _fee('DNU-SURCHARGE STRUCTURED FINES', '69.60'),
        _fee('DNU-FINES AND FORFEITED BAIL-STRUCTURED FINES', '232.00'),
        _fee('DNU-TIME PAYMENT FEES-STRUCTURED FINES', '10.00'),
        _fee('INDIGENT DEFENSE-MISDM-REIMBURSE STATE', '6.00'),
        _fee('DNU-INDIGENT DEFENSE-STRUC FINES-REIMB STATE', '6.00'),
        _fee('DNU-INDIGENT DEFENSE-STRUC FINES-REIMB STATE', '18.00'),
    ],
}

# The shape of the victim surcharge case. The surcharge is the last line, and
# without it the SURCHARGE category is $100.00 short of what ICOS says.
VICTIM_SURCHARGE = {
    'id': '00000  FECR000000',
    'summary_categories': _five(COSTS='1365.78', FINE='1250.00',
                                SURCHARGE='537.50'),
    'financials': [
        _fee('FILING AND DOCKETING FEES CRIMINAL', '100.00'),
        _fee('DNU-SURCHARGE ALL STATE CHARGES', '218.75'),
        _fee('DNU-SURCHARGE ALL STATE CHARGES', '218.75'),
        _fee('DNU-FINES/FORFEITED BAIL/CIVIL PENALTY', '625.00'),
        _fee('DNU-FINES/FORFEITED BAIL/CIVIL PENALTY', '625.00'),
        _fee('INDIGENT DEFENSE-MISC-REIMBURSE STATE', '109.75'),
        _fee('INDIGENT DEFENSE-MISC-REIMBURSE STATE', '82.50'),
        _fee('INDIGENT DEFENSE-MISC-REIMBURSE STATE', '68.75'),
        _fee('INDIGENT DEFENSE-MISC-REIMBURSE STATE', '82.50'),
        _fee('INDIGENT DEFENSE-MISC-REIMBURSE STATE', '82.50'),
        _fee('INDIGENT DEFENSE-FELONY-REIMBURSE STATE', '839.78'),
        _fee('DOMESTIC/SEXUAL ABUSE, STALKING, HUMAN TRAFF VICTIM SURCH',
             '100.00'),
    ],
}


# -- what the client reads off the workbook ----------------------------------

def test_indigent_defence_collected_as_a_structured_fine_is_still_indigent_defence():
    columns, note = crs.reconcile_financials(STRUCTURED_FINE)
    assert columns is not None, 'the whole row fell back to the summary'
    assert note is None, note
    assert columns == {
        'J': Decimal('30.00'),    # INDIGENT DEFENSE, the three reimbursements
        'O': Decimal('55.00'),    # MISCELLANEOUS, docket, reporter, time payment
        'Q': Decimal('69.60'),    # SURCHARGES
        'R': Decimal('232.00'),   # FINES, the fine itself and nothing else
    }, columns


def test_the_structured_fine_row_still_adds_up_to_what_icos_says():
    columns, _ = crs.reconcile_financials(STRUCTURED_FINE)
    owed = sum(category['due']
               for category in STRUCTURED_FINE['summary_categories'])
    assert sum(columns.values()) == owed == Decimal('386.60'), columns


def test_an_abbreviated_victim_surcharge_reconciles_as_a_surcharge():
    columns, note = crs.reconcile_financials(VICTIM_SURCHARGE)
    assert columns is not None, 'the whole row fell back to the summary'
    # The note names categories that could not be broken down. Before the fix
    # it named SURCHARGE and OTHER, and neither was really in doubt: the $100
    # was in the wrong one of the two.
    assert note is None, note
    assert columns == {
        'J': Decimal('1265.78'),
        'O': Decimal('100.00'),
        'Q': Decimal('537.50'),   # $437.50 of state surcharge plus the $100
        'R': Decimal('1250.00'),
    }, columns


# -- the classification itself ------------------------------------------------

def test_a_structured_fine_component_is_a_cost():
    for detail in ('DNU-INDIGENT DEFENSE-STRUC FINES-REIMB STATE',
                   'DNU-COURT REPORTER SERVICES STRUC FINE',
                   'DNU-TIME PAYMENT FEES-STRUCTURED FINES',
                   'DNU-DOCKET PROC - STRUCT FINE ABOVE SIMP'):
        assert crs.get_summary_bucket(detail) == 'COSTS', detail


def test_the_fine_in_a_structured_fine_is_still_a_fine():
    detail = 'DNU-FINES AND FORFEITED BAIL-STRUCTURED FINES'
    assert crs.get_summary_bucket(detail) == 'FINE'
    assert crs.get_finance_column(detail) == 'R'


# Every wording in the 400 case corpus carrying one of the words this change
# reorders around, with the bucket and the column it has to keep. The fix works
# by testing four specific fees before the general test for FINE, and the way
# that goes wrong is by catching something it was never meant to.
CLASSIFICATION = (
    ('STATE FINES/COUNTY SPEED,WEIGHT,COPYCAT ORDINANCES', 'FINE', 'R'),
    ('DNU-FINES/FORFEITED BAIL/CIVIL PENALTY', 'FINE', 'R'),
    ('FINE-DRIVING-NO PROOF OF INSURANCE', 'FINE', 'R'),
    ('DNU-FINES AND FORFEITED BAIL-STRUCTURED FINES', 'FINE', 'R'),
    ('DNU-CITY/COUNTY FINES/FORFEITED BAIL', 'FINE', 'R'),
    ('CITY FINES', 'FINE', 'R'),
    ('DOT FINES', 'FINE', 'R'),
    ('DNU-CITY FINES AND FORFEITED BAIL-NON-TRAFFIC', 'FINE', 'R'),
    ('DEFERRED JUDGMENT CIVIL PENALTY A-R', 'FINE', 'R'),
    ('DNU-SURCHARGE ALL STATE CHARGES', 'SURCHARGE', 'Q'),
    ('DNU-LAW ENFORCEMENT INITIATIVE SURCHARGE', 'SURCHARGE', 'Q'),
    ('CRIME SERVICES SURCHARGE', 'SURCHARGE', 'Q'),
    ('DNU-DRUG ABUSE SURCHARGE', 'SURCHARGE', 'Q'),
    ('DNU-SURCHARGE ALL LOCAL CHARGES', 'SURCHARGE', 'Q'),
    ('DNU-COUNTY ENFORCEMENT SURCHARGE', 'SURCHARGE', 'Q'),
    ('DNU-CONTEMPT OF DOM ABUSE PROTECTIVE ORDER SURCHARGE', 'SURCHARGE', 'Q'),
    ('DNU-SURCHARGE, FOR ALL LOCAL CHARGES', 'SURCHARGE', 'Q'),
    ('DNU-SURCHARGE STRUCTURED FINES', 'SURCHARGE', 'Q'),
    # Column P because a delinquent revolving fund obligation is money ICOS
    # will not say the nature of, which is a separate open question.
    ('DNU-DELINQUENT REVOLVING FUND OBLIGATION--SURCHARGES', 'SURCHARGE', 'P'),
    ('DOMESTIC/SEXUAL ABUSE, STALKING, HUMAN TRAFF VICTIM SURCH',
     'SURCHARGE', 'Q'),
    ('INDIGENT DEFENSE-FELONY-REIMBURSE STATE', 'COSTS', 'J'),
    ('INDIGENT DEFENSE-MISDM-REIMBURSE STATE', 'COSTS', 'J'),
    ('INDIGENT DEFENSE-MISC-REIMBURSE STATE', 'COSTS', 'J'),
    ('INDIGENT DEFENSE-MISDEMEANOR-REIMBURSE COUNTY', 'COSTS', 'J'),
    ('COURT REPORTER SERVICES', 'COSTS', 'O'),
    ('DNU-DOCKET PROC - STRUCT FINE ABOVE SIMP', 'COSTS', 'O'),
    ('DNU-INDIGENT DEFENSE-STRUC FINES-REIMB STATE', 'COSTS', 'J'),
    ('DNU-COURT REPORTER SERVICES STRUC FINE', 'COSTS', 'O'),
    ('DNU-TIME PAYMENT FEES-STRUCTURED FINES', 'COSTS', 'O'),
)


def test_every_fee_with_fine_or_surcharge_in_its_wording_is_classified():
    wrong = []
    for detail, bucket, column in CLASSIFICATION:
        got = (crs.get_summary_bucket(detail), crs.get_finance_column(detail))
        if got != (bucket, column):
            wrong.append('%s: wanted %s, got %s' % (detail, (bucket, column),
                                                    got))
    assert not wrong, wrong


def test_nothing_reads_the_abbreviation_as_a_word_of_its_own():
    """SURCH is short for SURCHARGE and never appears inside anything else.

    This is the risk the abbreviation carries: matching two letters short of a
    word matches more than the word. Across 400 captured cases nothing does,
    and the corpus is the only place that can be checked, so this pins the
    wordings that are in it.
    """
    for detail, bucket, _ in CLASSIFICATION:
        if 'SURCH' in detail:
            assert bucket == 'SURCHARGE', detail
        if bucket == 'SURCHARGE':
            assert 'SURCH' in detail, detail
