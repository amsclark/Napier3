"""A case ICOS has not adjudicated, and what the workbook is entitled to say.

ICOS prints each count twice: the charge as filed, then the adjudication. Napier
read only the second one, so a count with nothing adjudicated yet came out
empty, and an empty disposition was recorded as NOTF, NOT FILED.

Nothing about that was true. Of the three real cases it landed on, one was filed
eleven days earlier and still open, one ICOS dismissed in 2021, one ICOS closed
in 1993. All three have a case number, so none of them was never filed.

It was not a cosmetic label either, and the workbook is what says so:

  EXPUNGEMENT & 910.7 column I, "DISM ACQ?", reads NOTF alongside DISM, ACQ,
  WTHD and TNSF and answers YES, eligible under 901C.2. An open charge was
  being reported as expungeable.

  EXPUNGEMENT & 910.7 column G is "POSSIBLE PENDING CHARGES" and counts case
  rows whose disposition date is blank. Napier dropped the case-level date ICOS
  gave it, so a case closed in 1993 was counted as a live charge hanging over
  the client, and a pending charge is the thing that blocks expungement.

  SOL, BANKRUPTCY and EXEMPTIONS each render column G as
  IF(G=0, "open charge", G). A blank is how this workbook already says pending.
  Napier never produced one.

The pages here are synthetic. This repo is public and a real charges page is one
person's unredacted criminal record.
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


def _cells(*cells):
    return '<tr>%s</tr>' % ''.join(
        '<td><font size="2">%s</font></td>' % cell for cell in cells)


def charges_page(statute, description, outcome=None):
    """One count in ICOS's real shape: charged, then adjudicated.

    outcome of None is the page ICOS serves before a court has ruled. The
    adjudication block is present and its cells are empty, which is exactly what
    the three real captures look like.
    """
    html = ['<html><body><table>',
            _cells('Count 01', 'Original Charge'),
            _cells('Charge:', statute, 'Description:', description),
            _cells('Offense Date:', '01/01/1900', 'Arrest Date:', ''),
            _cells('Adjudication')]
    if outcome is None:
        html.append(_cells('Charge:', '', 'Description:', ''))
        html.append(_cells('Adjudication:', '', 'Adjudication Date:', ''))
    else:
        html.append(_cells('Charge:', statute, 'Description:', description))
        html.append(_cells('Adjudication:', outcome,
                           'Adjudication Date:', '02/02/1901'))
    html.append('</table></body></html>')
    return ''.join(html).encode('utf-8')


def parse(statute, description, outcome=None):
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(charges_page(statute, description, outcome),
                                   case)
    return case['charges'][0]


# -- the parser -------------------------------------------------------------

def test_the_charge_as_filed_is_kept():
    """It was read for the offence date and thrown away otherwise."""
    charge = parse('727.5', 'SYNTHETIC OBSTRUCTION')
    assert charge['original_charge'] == '727.5'
    assert charge['original_description'] == 'SYNTHETIC OBSTRUCTION'


def test_the_charge_as_filed_is_not_the_adjudicated_charge():
    """Column F is the adjudicated statute and this is not one.

    What the State accused someone of is not what a court decided. If the two
    ever merge, an unadjudicated statute reaches the expungement sheet as a
    charge held against the client, which is the bug this file's neighbour
    covers from the other side.
    """
    charge = parse('727.5', 'SYNTHETIC OBSTRUCTION')
    assert charge['charge'] == ''
    assert charge['description'] == ''


def test_an_unadjudicated_count_is_not_called_not_filed():
    dominant = crs.get_dominant_charge([parse('727.5', 'SYNTHETIC')])
    assert dominant['disposition'] == ''


def test_an_adjudicated_count_still_codes_from_the_adjudication():
    """The guard. Reading the charge as filed must not disturb the 87 of 90
    captured pages that have an adjudication and were always right."""
    dominant = crs.get_dominant_charge([parse('124.401', 'SYNTHETIC', 'GUILTY')])
    assert dominant['disposition'] == 'GTR'
    assert dominant['description'] == 'SYNTHETIC'
    assert dominant['charge'] == '124.401'


# -- into the workbook ------------------------------------------------------

def _case(statute, description, outcome=None, status='', dispo_date=''):
    case = {'id': '00000  SMSM000000', 'county': 'SYNTHETIC',
            'financials': [], 'summary_categories': [], 'sentences': [],
            'summary_created_date': '01/01/1900',
            'summary_disposition_date': dispo_date,
            'summary_dispo_status': status}
    case_parser.parse_case_charges(charges_page(statute, description, outcome),
                                   case)
    return case


def _row(case):
    sheet = load_workbook(FULL)['CASE DATA']
    unknown = crs.process_case(case, sheet, crs.FIRST_CASE_ROW, CLINIC)
    row = str(crs.FIRST_CASE_ROW)
    return {column: sheet[column + row].value
            for column in ('D', 'E', 'F', 'G', 'V')}, unknown


def test_a_pending_case_says_what_it_is_about():
    """The row carried a county, a filing date and a balance, and no charge.

    On the real one that was $89.50 owed on a case whose description column was
    empty, so nothing on the sheet said what the client had been accused of.
    """
    cells, _ = _row(_case('321.285', 'SYNTHETIC EXCESSIVE SPEED'))
    assert cells['E'] == 'SYNTHETIC EXCESSIVE SPEED'


def test_a_pending_case_leaves_column_g_blank():
    """Blank is this workbook's own word for it, in three sheets' formulas."""
    cells, _ = _row(_case('321.285', 'SYNTHETIC EXCESSIVE SPEED'))
    assert cells['G'] in (None, '')


def test_a_pending_charge_keeps_its_statute_out_of_column_f():
    """Naming the charge must not smuggle it onto the expungement sheet."""
    cells, _ = _row(_case('321.285', 'SYNTHETIC EXCESSIVE SPEED'))
    assert cells['F'] in (None, '')


def test_a_pending_case_still_counts_as_a_pending_charge():
    """Column D stays blank, which is how the expungement sheet detects it.

    The date only ever comes from ICOS. A case ICOS gives no disposition date
    for has none, so this row goes on setting off POSSIBLE PENDING CHARGES.
    """
    cells, _ = _row(_case('321.285', 'SYNTHETIC EXCESSIVE SPEED'))
    assert cells['D'] in (None, '')


def test_a_case_icos_dismissed_is_dismissed_rather_than_never_filed():
    """ICOS answered on the summary page and Napier used the answer civilly only.

    The real one was dismissed in 2021 with no per-count adjudication
    recorded, and Napier wrote NOTF and no date, contradicting the ICOS page it
    had just read.
    """
    cells, unknown = _row(_case('727.5', 'SYNTHETIC OBSTRUCTION',
                                status='DISMISSED', dispo_date='04/29/1902'))
    assert cells['G'] == 'DISM'
    assert cells['D'] == '04/29/1902'
    assert unknown == []


def test_a_closed_case_stops_counting_as_a_pending_charge():
    """A 1993 case was being counted as a live charge blocking expungement.

    CLOSED is not a disposition Napier can code, and the date is a fact ICOS
    stated either way. Taking the date is what stops the false pending charge.

    Column G used to stay blank here, on the reasoning that the disposition
    really was unknown. Iowa Legal Aid asked for OTH instead on 5 August,
    because blank is 0 to Excel and SOL, BANKRUPTCY and EXEMPTIONS all render
    IF(G=0, "open charge", G), so honest silence was printing as a live charge
    on three sheets. OTH is in no formula in either template, so the label is
    the only thing that changed. tests/test_ari_aug5_items.py holds that down
    from the other side, including the part where the wording is still
    reported.
    """
    cells, unknown = _row(_case('CR/OLDCASE', 'SYNTHETIC OLD CASE CODE',
                                status='CLOSED', dispo_date='09/23/1901'))
    assert cells['D'] == '09/23/1901'
    assert cells['G'] == 'OTH'
    assert unknown == [('CLOSED', True)]


def test_a_status_napier_cannot_read_is_reported_not_guessed():
    """The case-level vocabulary is not the per-count one and mostly does not
    overlap. Whether VIOLATIONS HANDLED BY CLERK is a guilty plea is a question
    about Iowa practice, and five sheets key formulas on the answer, so it
    travels out the channel that already writes column V and tells the run.
    """
    cells, unknown = _row(_case('321.285', 'SYNTHETIC SPEED',
                                status='VIOLATIONS HANDLED BY CLERK',
                                dispo_date='03/03/1903'))
    assert unknown == [('VIOLATIONS HANDLED BY CLERK', False)]
    assert 'VIOLATIONS HANDLED BY CLERK' in cells['V']
    assert cells['G'] in (None, '')


def test_an_adjudicated_case_ignores_the_case_level_status():
    """The count is the better answer and it wins wherever ICOS gave one.

    Written with the two disagreeing on purpose, because a fallback that is not
    fenced off stops being a fallback.
    """
    cells, unknown = _row(_case('124.401', 'SYNTHETIC FELONY', 'GUILTY',
                                status='DISMISSED', dispo_date='04/29/1902'))
    assert cells['G'] == 'GTR'
    assert cells['D'] == '02/02/1901'
    assert cells['E'] == 'SYNTHETIC FELONY'
    assert cells['F'] == '124.401'
    assert unknown == []
