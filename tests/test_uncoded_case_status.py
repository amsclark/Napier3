"""A case Iowa Courts has closed, on a row three sheets read as an open charge.

Napier's production alert fired on a real run with two of these:

    unrecognised disposition on an ICOS case (disposition CLOSED)

The status ICOS prints for a case as a whole is its own vocabulary, and
case_level_code deliberately translates only the one wording that overlaps the
per-count vocabulary. Everything else returns None, which leaves column G empty
and raises the alert rather than guessing at a conviction code. That part is
working as intended and the question of what CLOSED deserves belongs to Iowa
Legal Aid.

What was not working is what the row then said about itself. Both the empty
column G and an unreadable per-count adjudication went out under one note, and
that note describes the per-count case: it tells the attorney the row is coded
OTH and then explains how each sheet reads OTH. On the case-level row none of
that is true. Column G is empty, and

    BANKRUPTCY B4  =IF('CASE DATA'!A4<>"",IF('CASE DATA'!G4=0, "open charge", 'CASE DATA'!G4),"")

is also EXEMPTIONS B4 and SOL B4, so all three answer "open charge". The one
safety net on the row was describing a different row.

Of 300 captured cases 2 take this path, one carrying $197.43 of debt. Every case
number here is synthetic. The repo is public.
"""

import os
import sys
from decimal import Decimal

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs
from test_multi_count import charges_page

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')

CLOSED = 'CLOSED'
TRANSFERRED = 'TRANSFERRED'


def _row(counts, status, costs='197.43'):
    """A row built the way process_case builds one, off parsed ICOS pages."""
    sheet = load_workbook(FULL)['CASE DATA']
    case = {'id': '00000  FECR000000', 'county': 'SYNTHETIC',
            'financials': [], 'sentences': [],
            'summary_created_date': '01/01/1900',
            'summary_disposition_date': '02/02/1901',
            'summary_dispo_status': status}
    case_parser.parse_case_charges(charges_page(counts), case)
    case['summary_categories'] = [
        {'label': label,
         'original': Decimal(costs) if label == 'COSTS' else Decimal('0'),
         'paid': Decimal('0'),
         'due': Decimal(costs) if label == 'COSTS' else Decimal('0')}
        for label in ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER')]
    case['total_due'] = '$' + costs
    crs.process_case(case, sheet, crs.FIRST_CASE_ROW)
    row = str(crs.FIRST_CASE_ROW)
    return {column: sheet[column + row].value
            for column in ('D', 'G', 'V')}


UNADJUDICATED = [('714.2(3)', 'SYNTHETIC THEFT', None, '')]
UNREADABLE = [('714.2(3)', 'SYNTHETIC THEFT',
               'SYNTHETIC WORDING NOBODY HAS SEEN', '02/02/1901')]


# -- the shape the alert is firing on ----------------------------------------

@pytest.mark.parametrize('status', [CLOSED, TRANSFERRED])
def test_an_untranslated_case_status_leaves_column_g_empty(status):
    """The guard on the deliberate half. Guessing a code here is the error this
    is avoiding, so the fix must not start filling the cell in."""
    assert not _row(UNADJUDICATED, status)['G']


@pytest.mark.parametrize('status', [CLOSED, TRANSFERRED])
def test_the_row_says_the_three_sheets_will_call_it_an_open_charge(status):
    """What the attorney has to know to use the row: it is about to appear on
    BANKRUPTCY, EXEMPTIONS and SOL as a charge still pending."""
    note = _row(UNADJUDICATED, status)['V'] or ''
    assert 'open charge' in note, note
    for sheet in ('BANKRUPTCY', 'EXEMPTIONS', 'SOL'):
        assert sheet in note, note


def test_the_row_names_the_status_icos_printed():
    """Left out, the note sends someone back to ICOS without saying what to
    look for, and the wording is the whole reason the row is uncoded."""
    assert CLOSED in (_row(UNADJUDICATED, CLOSED)['V'] or '')


def test_the_row_does_not_claim_to_be_coded_oth():
    """The defect. Column G is empty, so every sentence the OTH note spends on
    how the sheets read OTH is about some other row."""
    note = _row(UNADJUDICATED, CLOSED)['V'] or ''
    assert 'OTH' not in note, note


def test_a_paid_off_case_is_told_as_well():
    """One of the two captured cases owes nothing. The mislabelling is on the
    three sheets that sort a case, not only on the ones that sort its money, so
    this note is not conditional on the row owing anything."""
    note = _row(UNADJUDICATED, TRANSFERRED, costs='0')['V'] or ''
    assert 'open charge' in note, note


# -- and the row the old note was written for still gets it ------------------

def test_a_count_napier_could_not_read_is_still_coded_oth():
    cells = _row(UNREADABLE, '')
    assert cells['G'] == 'OTH'
    assert 'coded OTH' in (cells['V'] or ''), cells['V']


def test_that_row_is_not_told_it_will_read_as_an_open_charge():
    """Because it will not. It has a code, and the sheets read the code."""
    assert 'open charge' not in (_row(UNREADABLE, '')['V'] or '')


def test_the_case_status_is_not_reached_when_a_count_was_adjudicated():
    """ICOS prints a case-level status on cases that do have adjudications. It
    is only consulted when no count has one, so a readable conviction is not
    displaced by a status nobody translates."""
    cells = _row([('714.2(3)', 'SYNTHETIC THEFT', 'GUILTY', '02/02/1901')],
                 CLOSED)
    assert cells['G'] == 'GTR'
    assert not cells['V'] or 'open charge' not in cells['V']


# -- the wording, checked against the template it describes ------------------

def test_the_three_sheets_really_do_read_an_empty_column_g_that_way():
    """The note makes a claim about the workbook. If a later template changes
    the formula, this fails rather than the note quietly going wrong."""
    book = load_workbook(FULL)
    row = str(crs.FIRST_CASE_ROW)
    for sheet in ('BANKRUPTCY', 'EXEMPTIONS', 'SOL'):
        formula = book[sheet]['B' + row].value or ''
        assert 'open charge' in formula, (sheet, formula)
        assert "'CASE DATA'!G" + row in formula, (sheet, formula)
