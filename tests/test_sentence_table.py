"""The sentence table, the term arithmetic, and why column I stays blank.

CASE DATA column I is "Under supervison?" [sic]. The expungement sheet reads it
to decide whether to fill in "Amount of debt subject to 910.7?", and reads a
blank as no, so that column has been "n/a" on every row of every workbook Napier
has ever produced.

Napier can very nearly answer it. The charges page carries a sentence table
under each count with the type, the date imposed and the duration, and a build
that read it and wrote YES when a probation term was still running on paper
went to Iowa Legal Aid on 3 August 2026.

They turned it down, and the reason belongs in the file, because nothing about
the code was wrong and a future reader will otherwise find a parser sitting one
line away from a column it could fill. ICOS does not record an early discharge,
records an extension only sometimes, and never carries parole, because parole is
corrections rather than the court. So the answer they need is not on the page at
any level of parsing care. Their words: ICOS "is not going to be reliable
because sometime they don't update if probation was pushed out or ended early",
and they check each client against the Department of Corrections website.

A wrong YES puts debt in a 910.7 column where it does not belong, so the column
stays with the people who can answer it.

What is tested here is what survives: the sentence table is still read onto the
case, and the term arithmetic is still what the payment history measures its
window with. Plus the one thing that must not drift back, which is that nothing
writes to column I.
"""

import os
import sys
from datetime import date

import pytest
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FULL = os.path.join(ROOT, 'CRS 3.5.5.xlsx')

CLINIC = date(2026, 7, 31)


def _sentence(kind, day, duration):
    return {'type': kind, 'date': day, 'duration': duration}


# -- the term arithmetic ----------------------------------------------------
# Kept because the payment history measures its window with it: the twelve month
# recent-payments figure that feeds ability to pay is _add_term(as_of, -12,
# 'Month'), so a month counted wrong here moves a dollar figure.

def test_a_year_term_is_a_calendar_year_not_365_days():
    """A leap year in the middle of the term is the case that separates them."""
    assert crs._add_term(date(2024, 1, 15), 1, 'Year') == date(2025, 1, 15)


def test_a_five_year_term_lands_on_the_same_day_five_years_later():
    assert crs._add_term(date(2001, 7, 2), 5, 'Year') == date(2006, 7, 2)


def test_a_term_starting_on_a_leap_day_falls_back_to_the_28th():
    assert crs._add_term(date(2024, 2, 29), 1, 'Year') == date(2025, 2, 28)


def test_a_term_starting_on_the_31st_falls_back_to_a_short_month():
    assert crs._add_term(date(2026, 1, 31), 1, 'Month') == date(2026, 2, 28)


def test_day_terms_are_counted_in_days():
    assert crs._add_term(date(1995, 3, 31), 365, 'Day') == date(1996, 3, 30)


def test_week_terms_are_counted_in_weeks():
    assert crs._add_term(date(2026, 1, 1), 2, 'Week') == date(2026, 1, 15)


# -- reading a date the way ICOS writes it ----------------------------------

@pytest.mark.parametrize('text', ['07/02/2001', ' 07/02/2001 '])
def test_an_icos_date_is_read(text):
    assert crs.parse_us_date(text) == date(2001, 7, 2)


@pytest.mark.parametrize('text', ['', None, 'n/a', '2001-07-02', '13/45/2001'])
def test_anything_else_is_not_guessed_at(text):
    assert crs.parse_us_date(text) is None


# -- the parser -------------------------------------------------------------

CHARGES_PAGE = b"""
<html><body><table>
<tr><td><font>Sentence</font></td><td><font>Sentence Date</font></td>
<td><font>Duration</font></td><td><font>Fine</font></td>
<td><font>Appeal</font></td><td><font>Judge</font></td>
<td><font>Facility Type</font></td><td><font>Attorney</font></td>
<td><font>Restitution</font></td><td><font>Drug</font></td>
<td><font>Extradition</font></td><td><font>Lic. Revoked</font></td>
<td><font>DDS</font></td><td><font>Batterer</font></td></tr>
<tr><td><font>PROBATION</font></td><td><font>07/02/2001</font></td>
<td><font>5 Year(s)</font></td><td><font></font></td>
<td><font></font></td><td><font>SYNTHETIC, JUDGE</font></td>
<td><font></font></td><td><font>N</font></td>
<td><font>N</font></td><td><font>N</font></td>
<td><font>N</font></td><td><font>N</font></td>
<td><font>N</font></td><td><font></font></td></tr>
</table></body></html>
"""


def test_the_sentence_row_is_read_off_the_page():
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(CHARGES_PAGE, case)
    assert case['sentences'] == [
        {'type': 'PROBATION', 'date': '07/02/2001', 'duration': '5 Year(s)'}]


def test_the_header_row_is_not_mistaken_for_a_sentence():
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(CHARGES_PAGE, case)
    assert all(s['type'] != 'Sentence' for s in case['sentences'])


def test_a_page_with_no_sentence_table_yields_none():
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(
        b"<html><body><table><tr><td><font>Nothing here</font></td></tr>"
        b"</table></body></html>", case)
    assert case['sentences'] == []


def test_a_renamed_column_stops_the_read_rather_than_shifting_it():
    """The header is matched exactly so that a changed page reads as no data."""
    page = CHARGES_PAGE.replace(b'Lic. Revoked', b'License Revoked')
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(page, case)
    assert case['sentences'] == []


# -- column I belongs to the staff ------------------------------------------

def _case(sentences, disposition='GUILTY'):
    return {
        'id': '00000  FECR000000', 'county': 'SYNTHETIC',
        'charges': [{'charge': '124.401', 'description': 'SYNTHETIC OFFENSE',
                     'disposition': [disposition], 'offenseDate': '01/01/1900',
                     'dispositionDate': '02/02/1901'}],
        'financials': [], 'summary_categories': [],
        'sentences': sentences,
    }


def _row(case):
    sheet = load_workbook(FULL)['CASE DATA']
    crs.process_case(case, sheet, crs.FIRST_CASE_ROW, CLINIC)
    row = str(crs.FIRST_CASE_ROW)
    return sheet['I' + row].value, sheet['V' + row].value


@pytest.mark.parametrize('sentences', [
    [],
    [_sentence('PROBATION', '01/01/2025', '3 Year(s)')],
    [_sentence('PROBATION', '01/01/1995', '1 Year(s)')],
    [_sentence('PROBATION - OTHER THAN DCS', '12/22/2025', '2 Year(s)')],
    [_sentence('PROBATION EXTENDED', '01/01/2025', '3 Year(s)')],
    [_sentence('DRUG COURT', '01/01/2025', '3 Year(s)')],
    [_sentence('RESIDENTIAL FACILITY', '01/01/2025', '3 Year(s)')],
    [_sentence('PRISON', '01/01/2025', '5 Year(s)')],
])
def test_column_i_is_left_for_staff_whatever_the_sentence_says(sentences):
    """The one that must not drift back.

    A term still running on paper is exactly the case Napier could answer and
    exactly the case Iowa Legal Aid asked it not to, because ICOS does not show
    them an early discharge. Every wording that used to produce a YES is here,
    so re-adding the feature fails eight tests rather than passing quietly into
    a hearing.
    """
    value, _ = _row(_case(sentences))
    assert value is None


def test_nothing_about_supervision_reaches_the_notes():
    """Column V carries money caveats staff have to read. A note about a column
    Napier does not fill is noise competing with those."""
    _, with_term = _row(_case([_sentence('PROBATION', '01/01/2025',
                                         '3 Year(s)')]))
    _, without = _row(_case([]))
    assert with_term == without
    for text in ('supervision', 'probation', 'parole', 'Column I'):
        assert not with_term or text not in with_term


def test_a_civil_case_leaves_column_i_alone_too():
    """The write used to sit outside the charge branch, so a case with no
    adjudication at all still reached it."""
    case = {
        'id': '00000  SCSC000000', 'county': 'SYNTHETIC',
        'charges': [], 'financials': [], 'summary_categories': [],
        'summary_created_date': '01/01/1900',
        'summary_disposition_date': '02/02/1901',
        'summary_dispo_status': 'SYNTHETIC STATUS',
        'sentences': [_sentence('PROBATION', '01/01/2025', '3 Year(s)')],
    }
    value, _ = _row(case)
    assert value is None
