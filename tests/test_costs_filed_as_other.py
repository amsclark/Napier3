"""Ten more fees the clerk files under COSTS and Napier read as OTHER.

The partition check in reconcile_financials compares each summary category to
the itemization lines Napier sorted into it, and when a fee is sorted into the
wrong category one runs short and the other runs over by the same amount. Of
the 400 captured cases, 36 failed that check, and on 27 of them the failure was
exactly this signature: COSTS short, OTHER over, equal to the cent.

On every one of the 27 the difference is accounted for by the same ten
wordings, each filed under COSTS by the clerk's own summary and read as OTHER
by Napier: REFUNDABLE, APPEARANCE BOND REFUND, LIENS ENTERING/ENDORSEMENT,
FINAL DECREE OF DISSOLUTION, COPY/BINDER FEES, SCHEDULED VIOLATION REQU CT
APPEAR, SMALL ESTATE ADMINISTRATION, CONFESSION OF JUDGMENT, DEFERRED PRAECIPE
FEE, and OTHER SIMPLE MISDEMEANORS. Ten alone explain a whole case each; one
Linn case takes a $500.00 REFUNDABLE and a $500.00 bond refund together to
cover its $1,000.00 difference. The counties span the state, twelve of them,
so this is clerk practice rather than one county's habit.

What it cost: a category that fails the check surrenders its fee breakdown and
comes back as a lump category total with a note telling the staffer it could
not be broken down. Measured across the corpus the fix clears that note from
eleven rows, shrinks it on three more, and takes the cases that reconcile in
full from 80 to 84. No money changes columns, because these fees land in
MISCELLANEOUS either way; what changes is whether the rest of the row keeps
its fee-by-fee breakdown and whether the note tells the truth.

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


# The shape of the failing cases: a refundable the summary counts in COSTS,
# alongside fees whose buckets were never in doubt. Before the fix the
# REFUNDABLE line reads as OTHER, COSTS comes up $500.00 short, OTHER comes up
# $500.00 over, and both categories surrender their breakdown.
REFUNDABLE_CASE = {
    'id': '00000  SMSM000000',
    'summary_categories': _five(COSTS='560.00', FINE='100.00',
                                SURCHARGE='15.00'),
    'financials': [
        _fee('FILING AND DOCKETING FEES CRIMINAL', '60.00'),
        _fee('REFUNDABLE', '500.00'),
        _fee('DNU-FINES/FORFEITED BAIL/CIVIL PENALTY', '100.00'),
        _fee('DNU-SURCHARGE ALL LOCAL CHARGES', '15.00'),
    ],
}


def test_a_refundable_the_summary_counts_in_costs_reconciles_there():
    columns, note = crs.reconcile_financials(REFUNDABLE_CASE)
    assert columns is not None, 'the whole row fell back to the summary'
    assert note is None, note
    assert sum(columns.values()) == Decimal('675.00'), columns


# The clerk files each of these under COSTS. Measured, not read off the
# wording: on 27 captured cases the COSTS shortfall equals the OTHER excess to
# the cent and these wordings are exactly the difference.
FILED_AS_COSTS = (
    'REFUNDABLE',
    'APPEARANCE BOND REFUND',
    'LIENS, ENTERING/ENDORSEMENT',
    'DNU-FINAL DECREE OF DISSOLUTION',
    'DNU-COPY/BINDER FEES',
    'DNU-SCHEDULED VIOLATION REQU CT APPEAR',
    'SMALL ESTATE ADMINISTRATION',
    'CONFESSION OF JUDGMENT - $5000 OR MORE A-R',
    'DEFERRED PRAECIPE FEE A-R',
    'DNU-OTHER SIMPLE MISDEMEANORS',
)


def test_the_ten_wordings_the_clerk_files_under_costs():
    wrong = [detail for detail in FILED_AS_COSTS
             if crs.get_summary_bucket(detail) != 'COSTS']
    assert not wrong, wrong


def test_the_wordings_these_markers_must_not_capture():
    """The markers are substrings, and the way a substring goes wrong is by
    matching a wording it was never meant to. Everything in the corpus these
    new markers could plausibly touch, with the bucket it has to keep."""
    for detail, bucket in (
            # Held money that was already filed under COSTS by an override.
            ('REFUNDABLES DUE TO PREPAID EXPENSES', 'COSTS'),
            # PRAECIPE also appears in filing wordings that were COSTS anyway.
            ('FILING A PRAECIPE UNDER CHAPTER 654 A-R', 'COSTS'),
            ('FILING A PRAECIPE UNDER CHAPTER 626 A-R', 'COSTS'),
            # OTHER SIMPLE MISDEMEANORS with COURT COSTS in front, same thing.
            ('COURT COSTS - OTHER SIMPLE MISDEMEANORSR', 'COSTS'),
            # A scheduled violation wording that carries the FINE reading and
            # an override on purpose. The new CT APPEAR marker must not move it.
            ('DNU-SCHEDULED VIOLATION/NON-SCHEDULED', 'COSTS'),
            # A bare judgment is not a confession of judgment.
            ('JUDGMENTS', 'OTHER'),
            # The fine in the refundable case stays a fine.
            ('DNU-FINES/FORFEITED BAIL/CIVIL PENALTY', 'FINE'),
    ):
        assert crs.get_summary_bucket(detail) == bucket, detail
