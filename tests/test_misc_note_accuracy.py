"""A row that broke its balance out correctly, told it did not.

When a category will not reconcile against its own itemization, Napier takes
the balance from the summary and asks the itemization only which columns it
belongs in. Usually that is a split and the note says the split is an estimate.
Sometimes every fee in the category points at one column, and then there is no
estimate involved: the balance went to that column, exactly, and the note has
nothing to add.

The caveat that goes out with it was deciding what to say from the category's
label instead. COSTS and OTHER both fall to MISCELLANEOUS when a label is all
there is to go on, so any unreconciled COSTS balance was told it had landed in
MISCELLANEOUS whether it had or not.

Measured on the 300 captured cases, recalculated: five rows carry that claim
and one of them is false. Its MISCELLANEOUS cell holds $0.00 and the $6,577.56
the note is about is sitting in JAIL / ROOM & BOARD, broken out per fee. That
column is the whole basis of the Polk room-and-board appeal sheet and half of
the twenty year test on SOL, so a note telling an attorney the number is a
lump sum costs the client the remedy rather than the money.

Two smaller things in the same sentence. A category that placed no money at all
was named alongside the ones that did, and the list read "COSTS and OTHER is in
MISCELLANEOUS".

Every case number and amount below is synthetic. The repo is public.
"""

import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs

# A fine that reconciles to the cent. Without one category that adds up, the
# whole row is unreconciled and reconcile_financials hands it back to the
# caller, which says so in its own sentence and never reaches this note.
FINE_LINE = {'detail': 'FINE', 'amount': Decimal('65.00'), 'paid': None}
FINE_TOTAL = Decimal('65.00')

CLAIM = 'MISCELLANEOUS'


def _five(**due):
    """The five bucket summary ICOS prints, everything zero by default."""
    rows = []
    for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER'):
        original, paid = due.get(label, (Decimal('0'), Decimal('0')))
        rows.append({'label': label, 'original': original, 'paid': paid,
                     'due': original - paid})
    return rows


def _row(costs_original, lines):
    """A COSTS balance the itemization will not account for, and its note."""
    case = {
        'summary_categories': _five(
            COSTS=(Decimal(costs_original), Decimal('0')),
            FINE=(FINE_TOTAL, Decimal('0'))),
        'financials': [FINE_LINE] + list(lines),
    }
    columns, note = crs.reconcile_financials(case)
    assert columns is not None, 'the row fell back to the summary'
    return columns, note or ''


def _fee(detail, amount):
    return {'detail': detail, 'amount': Decimal(amount), 'paid': None}


# Fees whose column is not in doubt, with the column each one lands in.
ROOM_AND_BOARD = ('ROOM/BOARD FEES', 'L')
SHERIFF = ('SHERIFFS FEES - LOCAL', 'M')
INDIGENT_DEFENSE = ('INDIGENT DEFENSE-MISDM-REIMBURSE STATE', 'J')

# Nothing in this wording names a fee, so it really does fall to MISCELLANEOUS.
UNCATEGORISED = 'SYNTHETIC UNCATEGORISED LINE'


# -- the defect --------------------------------------------------------------

@pytest.mark.parametrize('detail,column',
                         [ROOM_AND_BOARD, SHERIFF, INDIGENT_DEFENSE])
def test_a_balance_that_landed_in_one_real_column_is_not_called_miscellaneous(
        detail, column):
    """Every fee in the category points at the same column, so the balance went
    there whole. There is no MISCELLANEOUS in it to warn about."""
    columns, note = _row('6577.56', [_fee(detail, '1000.00')])
    assert columns[column] == Decimal('6577.56')
    assert not columns.get('O')
    assert CLAIM not in note, note


def test_the_note_still_says_the_category_did_not_reconcile():
    """Only the claim about MISCELLANEOUS is wrong. That the balance is ICOS's
    category total rather than a per-fee breakdown is true and has to survive,
    or the fix has thrown away the warning instead of correcting it."""
    _columns, note = _row('6577.56', [_fee(ROOM_AND_BOARD[0], '1000.00')])
    assert 'COSTS' in note, note
    assert 'did not add up' in note, note


def test_a_category_that_placed_no_money_is_not_named():
    """An uncategorised line lands in the OTHER bucket, which the summary
    reports as $0.00. OTHER fails to reconcile and has no balance to put
    anywhere, so naming it sends someone to look at an empty cell."""
    columns, note = _row('100.00', [_fee(UNCATEGORISED, '10.00')])
    assert columns['O'] == Decimal('100.00'), 'the COSTS balance is in O'
    # The money in MISCELLANEOUS is the COSTS balance. OTHER contributed none.
    assert 'OTHER is in' not in note, note
    assert 'and OTHER' not in note.split('did not add up')[-1], note


# -- and the row the warning was written for still gets it -------------------

def test_a_balance_that_really_is_in_miscellaneous_is_still_called_out():
    """The guard. This is the case the sentence exists for, and it is the one
    where MISCELLANEOUS means the fee cannot be reasoned about on any sheet."""
    columns, note = _row('100.00', [_fee(UNCATEGORISED, '10.00')])
    assert columns['O'] == Decimal('100.00')
    assert CLAIM in note, note
    assert 'COSTS is in %s' % CLAIM in note, note


def test_an_apportioned_split_says_nothing_about_miscellaneous():
    """Unchanged behaviour, asserted because it is the other half of the same
    condition: a balance divided across columns is described by the estimate
    sentence, not this one."""
    columns, note = _row('100.00', [_fee(ROOM_AND_BOARD[0], '30.00'),
                                    _fee(SHERIFF[0], '20.00')])
    assert columns['L'] and columns['M']
    assert CLAIM not in note, note
    assert 'estimates' in note, note


def test_a_category_with_no_itemization_at_all_still_falls_to_its_label():
    """With no fees to read, the label is all there is and the balance really
    does go to MISCELLANEOUS. The note has to keep saying so."""
    columns, note = _row('100.00', [_fee('SYNTHETIC RESTITUTION LINE', '5.00')])
    # The COSTS bucket is empty, so the balance falls back to the label column.
    assert columns['O'] == Decimal('100.00')
    assert 'COSTS is in %s' % CLAIM in note, note


# -- the sentence itself -----------------------------------------------------

def test_two_stranded_categories_read_as_plural():
    """Naming two categories and then saying "is" reads as a bug in the row to
    anyone who notices, which is not what you want on the one line telling an
    attorney how much of the number to trust."""
    case = {
        'summary_categories': _five(COSTS=(Decimal('100.00'), Decimal('0')),
                                    OTHER=(Decimal('50.00'), Decimal('0')),
                                    FINE=(FINE_TOTAL, Decimal('0'))),
        'financials': [FINE_LINE,
                       _fee(UNCATEGORISED, '10.00'),
                       _fee('SYNTHETIC COURT COSTS LINE', '7.00')],
    }
    columns, note = crs.reconcile_financials(case)
    note = note or ''
    assert columns is not None
    assert CLAIM in note, note
    assert 'COSTS and OTHER are in' in note, note
    assert 'their own columns' in note, note
    assert 'OTHER is in' not in note, note


# -- the property the sentence is supposed to have ---------------------------

@pytest.mark.parametrize('lines', [
    [_fee(ROOM_AND_BOARD[0], '1000.00')],
    [_fee(SHERIFF[0], '50.00')],
    [_fee(INDIGENT_DEFENSE[0], '60.00')],
    [_fee(UNCATEGORISED, '10.00')],
    [_fee(ROOM_AND_BOARD[0], '30.00'), _fee(SHERIFF[0], '20.00')],
    [_fee(UNCATEGORISED, '10.00'), _fee(SHERIFF[0], '20.00')],
])
def test_the_claim_is_never_made_against_an_empty_miscellaneous_cell(lines):
    """What the note is allowed to say, stated as the rule rather than case by
    case: if the row says money is in MISCELLANEOUS then MISCELLANEOUS holds
    money. Every one of these shapes came off a real captured itemization."""
    columns, note = _row('100.00', lines)
    if CLAIM in note:
        assert columns.get('O'), (columns, note)
