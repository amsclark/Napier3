"""Iowa Legal Aid's 7 August workbook review: what column E says about a count.

Three of Ari's findings were about the same column. A conviction's description
carried a disposition suffix but not the charge class, so FORGERY[GPL] could be
anything from a scheduled violation to a class C felony and the reader had to
open ICOS to find out. A case pending on more than one count read [OTH];[OTH]
in column E and ';' in column F, so staff could not tell what the State had
accused the client of. And an out of county warrant is Iowa holding somebody
for another jurisdiction, not a conviction of theirs, but it is filed with no
statute so the civil rule keyed on statutes could not see it.

The pages here carry the row shapes of the real ones, including the detail the
first cut of the class suffix missed: the class labels ride mid-row, sharing a
row with 'DPS Number:' when filed and with 'Judge:' when adjudicated, so a
parser reading only the first cell of each row never sees them.

Synthetic pages throughout. The repository is public and a real charges page is
one person's unredacted criminal record.
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


def charges_page(counts):
    """ICOS's real row shapes, class rows included.

    counts is a list of dicts: statute, description, outcome, and optionally
    adjudicated_class. outcome of None is the page ICOS serves before a court
    has ruled: the adjudication block present, its cells empty, the class row
    printing a non-breaking space.
    """
    html = ['<html><body><table>']
    for number, count in enumerate(counts, start=1):
        html.append(_cells('Count %02d' % number, 'Original Charge'))
        html.append(_cells('Charge:', count['statute'],
                           'Description:', count['description']))
        html.append(_cells('Offense Date:', '01/01/1900', 'Arrest Date:', ''))
        html.append(_cells('DPS Number:', '&nbsp;', 'Charge Class:',
                           count.get('filed_class', '&nbsp;')))
        html.append(_cells('Adjudication'))
        if count['outcome'] is None:
            html.append(_cells('Charge:', '', 'Description:', ''))
            html.append(_cells('Adjudication:', '', 'Adjudication Date:', ''))
            html.append(_cells('Judge:', '&nbsp;',
                               'Adjudicated Charge Class:', '&nbsp;'))
        else:
            html.append(_cells('Charge:', count['statute'],
                               'Description:', count['description']))
            html.append(_cells('Adjudication:', count['outcome'],
                               'Adjudication Date:', '02/02/1901'))
            html.append(_cells('Judge:', 'SYNTHETIC, JUDGE A',
                               'Adjudicated Charge Class:',
                               count.get('adjudicated_class', '&nbsp;')))
    html.append('</table></body></html>')
    return ''.join(html).encode('utf-8')


def parse(counts):
    case = {'id': '00000  FECR000000'}
    case_parser.parse_case_charges(charges_page(counts), case)
    return case['charges'][0]


def _case(counts, status='', dispo_date=''):
    case = {'id': '00000  SMSM000000', 'county': 'SYNTHETIC',
            'financials': [], 'summary_categories': [],
            'sentences': [], 'summary_created_date': '01/01/1900',
            'summary_disposition_date': dispo_date,
            'summary_dispo_status': status}
    case_parser.parse_case_charges(charges_page(counts), case)
    return case


def _row(case):
    sheet = load_workbook(FULL)['CASE DATA']
    unknown = crs.process_case(case, sheet, crs.FIRST_CASE_ROW, CLINIC)
    i = str(crs.FIRST_CASE_ROW)
    cells = {column: sheet[column + i].value
             for column in ('D', 'E', 'F', 'G', 'V')}
    return cells, unknown


def count(statute, description, outcome, **extra):
    return dict(statute=statute, description=description, outcome=outcome,
                **extra)


# -- the charge class in column E -------------------------------------------


class TestChargeClassSuffixes:
    def test_the_class_lands_between_description_and_disposition(self):
        """Her exemplar shape: FORGERY[FELD][GPL], class first, result last."""
        charge = parse([count('715A.2', 'SYNTHETIC FORGERY',
                              'GUILTY - NEGOTIATED/VOLUN PLEA',
                              adjudicated_class='CLASS D FELONY')])
        assert charge['description'] == 'SYNTHETIC FORGERY[FELD][GPL]'

    @pytest.mark.parametrize('wording,suffix',
                             sorted(case_parser.CHARGE_CLASS_SUFFIXES.items()))
    def test_every_wording_in_the_map_reaches_the_column(self, wording, suffix):
        charge = parse([count('321.218', 'SYNTHETIC OFFENSE', 'GUILTY',
                              adjudicated_class=wording)])
        assert charge['description'] == \
            'SYNTHETIC OFFENSE[%s][GTR]' % suffix

    def test_each_count_wears_its_own_class(self):
        """Classes pair with counts by position, so a two-count case where the
        classes differ is the test that a shared index cannot fake."""
        charge = parse([count('715A.2', 'SYNTHETIC FORGERY', 'GUILTY',
                              adjudicated_class='CLASS D FELONY'),
                        count('321.218', 'SYNTHETIC SCHEDULED', 'GUILTY',
                              adjudicated_class='SCHEDULED VIOLATION')])
        assert charge['description'] == \
            'SYNTHETIC FORGERY[FELD][GTR];SYNTHETIC SCHEDULED[SV][GTR]'

    def test_a_blank_class_row_adds_no_suffix(self):
        """ICOS prints the row with a non-breaking space when it has nothing
        to say, which is most of the corpus before 2010."""
        charge = parse([count('321.218', 'SYNTHETIC OFFENSE', 'GUILTY')])
        assert charge['description'] == 'SYNTHETIC OFFENSE[GTR]'

    def test_a_wording_the_map_does_not_know_adds_no_suffix(self):
        """Deliberate. ICOS's class vocabulary is its own, and a wording this
        map has not vouched for going out as an invented bracket code would be
        Napier asserting something nobody checked. No suffix, no guess."""
        charge = parse([count('321.218', 'SYNTHETIC OFFENSE', 'GUILTY',
                              adjudicated_class='OTHER')])
        assert charge['description'] == 'SYNTHETIC OFFENSE[GTR]'

    def test_a_dismissed_count_still_shows_its_class(self):
        """The class describes the charge, not the conviction, so it stays on
        a dismissed count's description while the statute still stays out of
        column F."""
        charge = parse([count('124.401(5)', 'SYNTHETIC POSSESSION',
                              'DISMISSED',
                              adjudicated_class='AGGRAVATED MISDEMEANOR')])
        assert charge['description'] == 'SYNTHETIC POSSESSION[AGMS][DISM]'
        assert charge['charge'] == ''

    def test_the_suffix_survives_into_the_workbook_column(self):
        cells, _ = _row(_case([count('123.46(2)', 'SYNTHETIC INTOXICATION',
                                     'GUILTY - OTHER',
                                     adjudicated_class='SIMPLE MISDEMEANOR')]))
        assert cells['E'] == 'SYNTHETIC INTOXICATION[SMMS][GTR]'

    def test_the_suffix_codes_are_iowa_legal_aids_own(self):
        """Her 8/18 correction, pinned: FELA through FELD, AGMS, SRMS, SMMS,
        SV, NSV -- plus CNTP for the contempt rows she asked for by example.
        The first cut shipped AGMD/SRMD/SMMD, which read fine and were codes
        nobody at Iowa Legal Aid writes. The right-hand side of the map is
        her staff's vocabulary, not a free choice, so a new entry whose code
        is not on this list has to be one she has asked for."""
        assert sorted(set(case_parser.CHARGE_CLASS_SUFFIXES.values())) == [
            'AGMS', 'CNTP', 'FELA', 'FELB', 'FELC', 'FELD', 'NSV', 'SMMS',
            'SRMS', 'SV']

    def test_a_non_scheduled_violation_reads_nsv(self):
        """The wording is ICOS's, confirmed by replaying the five Polk NTA
        cases Iowa Legal Aid named, on 18 August 2026: every one prints
        NON-SCHEDULED VIOLATION with the hyphen, as does the one earlier
        captured NTA page from another county. This test carried both
        spellings until then and narrowed to the one the real pages use."""
        charge = parse([count('321.218', 'SYNTHETIC DENIED', 'GUILTY',
                              adjudicated_class='NON-SCHEDULED VIOLATION')])
        assert charge['description'] == 'SYNTHETIC DENIED[NSV][GTR]'

    def test_an_unmapped_spelling_adds_no_suffix_rather_than_a_guess(self):
        """The no-guess rule, held to on the spelling that was dropped."""
        charge = parse([count('321.218', 'SYNTHETIC DENIED', 'GUILTY',
                              adjudicated_class='NON SCHEDULED VIOLATION')])
        assert charge['description'] == 'SYNTHETIC DENIED[GTR]'


# -- a case pending on more than one count ----------------------------------


class TestAMultiCountPendingCase:
    PENDING = [count('321.561', 'SYNTHETIC BARRED', None),
               count('321.218', 'SYNTHETIC DENIED', None)]

    def test_the_counts_show_as_filed(self):
        """Her AGCR400914: two open counts read [OTH];[OTH] in column E and
        ';' in column F. The single-count path already showed the charge as
        filed; this is the same rule reaching the multi-count path."""
        charge = parse(self.PENDING)
        assert charge['description'] == 'SYNTHETIC BARRED;SYNTHETIC DENIED'
        assert charge['charge'] == ''

    def test_no_count_wears_a_synthetic_suffix(self):
        """No suffix at all: disposition_code cannot name an absent result,
        and [OTH] was the parser talking about its own confusion."""
        assert '[' not in parse(self.PENDING)['description']

    def test_a_pending_count_beside_a_conviction_stays_as_filed(self):
        """The mixed case: the adjudicated count keeps its suffix and its
        statute, the open one is described as filed and keeps out of F."""
        charge = parse([count('124.401', 'SYNTHETIC CONTROLLED', 'GUILTY'),
                        count('321.218', 'SYNTHETIC DENIED', None)])
        assert charge['description'] == \
            'SYNTHETIC CONTROLLED[GTR];SYNTHETIC DENIED'
        assert charge['charge'] == '124.401'

    def test_the_row_still_reads_as_an_open_case(self):
        """The guard the fix must not break: blank D is how the EXPUNGEMENT
        sheet counts pending charges, and blank G is what SOL, BANKRUPTCY and
        EXEMPTIONS render as "open charge". Describing the counts must not
        smuggle in a date or a code."""
        cells, _ = _row(_case(self.PENDING))
        assert cells['D'] in (None, '')
        assert cells['F'] in (None, '')
        assert cells['G'] in (None, '')
        assert cells['E'] == 'SYNTHETIC BARRED;SYNTHETIC DENIED'


# -- out of county warrant ---------------------------------------------------


class TestOutOfCountyWarrant:
    """The fugitive holds ICOS files with no statute worth matching.

    The 5 August civil rule reads statutes, and these counts cite none, so a
    clerk's disposition wording still decided column G. Iowa Legal Aid asked
    for the civil reading to reach them by name.
    """

    def test_it_reads_civil_whatever_the_clerk_typed(self):
        cells, _ = _row(_case([count('', 'OUT OF COUNTY WARRANT', 'GUILTY')]))
        assert cells['G'] == 'CIV'

    def test_the_class_suffix_does_not_hide_the_wording(self):
        """The name match strips suffixes, and after the class change there
        can be two of them stacked."""
        cells, _ = _row(_case([count('', 'OUT OF COUNTY WARRANT', 'GUILTY',
                                     adjudicated_class='SIMPLE MISDEMEANOR')]))
        assert cells['G'] == 'CIV'

    def test_a_dismissed_one_keeps_its_cleared_code(self):
        """Same protection as the 820.2 holds: DISM clears the expungement
        sheet and CIV does not, so the label stands where the label is true."""
        cells, _ = _row(_case([count('', 'OUT OF COUNTY WARRANT',
                                     'DISMISSED')]))
        assert cells['G'] == 'DISM'

    def test_a_conviction_alongside_it_wins(self):
        """Every count, not any count -- the same rule as the statute path."""
        cells, _ = _row(_case([count('', 'OUT OF COUNTY WARRANT', 'GUILTY'),
                               count('124.401', 'SYNTHETIC CONTROLLED',
                                     'GUILTY')]))
        assert cells['G'] == 'GTR'

    def test_two_of_them_together_are_still_civil(self):
        cells, _ = _row(_case([count('', 'OUT OF COUNTY WARRANT', 'GUILTY'),
                               count('', 'OUT OF COUNTY WARRANT', 'GUILTY')]))
        assert cells['G'] == 'CIV'

    def test_some_other_unmatched_description_is_not_civil(self):
        """The list is a list, not a heuristic. A statute-less count with a
        wording nobody vouched for keeps whatever the disposition said."""
        cells, _ = _row(_case([count('', 'SYNTHETIC MYSTERY HOLD',
                                     'GUILTY')]))
        assert cells['G'] == 'GTR'
