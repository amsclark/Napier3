"""Which disposition speaks for the case: column G of the CRS.

A plea deal reaches ICOS as several counts with different outcomes, one
guilty and the rest dismissed. The CRS has a single column for it, so
get_dominant_charge ranks them and picks one. That ranking had no tests on
it at all, and it decides whether a case reads as a conviction.

Sampled against seventy real cases in July 2026: eight of them carried more
than one distinct disposition, and the shapes below are the ones that
actually turned up. Identifiers here are synthetic; this repo is public.
"""

import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_parser
import crs


def _charge(*dispositions):
    return [{'description': 'SYNTHETIC OFFENSE', 'disposition': list(dispositions),
             'offense_date': '01/01/1900', 'disposition_date': '02/02/1901'}]


def test_a_guilty_count_outranks_the_dismissed_ones():
    """The shape of a plea deal. Pleading to one count dismisses the rest."""
    charge = _charge('DISMISSED BY COURT', 'GUILTY - NEGOTIATED/VOLUN PLEA',
                     'DISMISSED BY COURT')
    assert crs.get_dominant_charge(charge)['disposition'] == 'GPL'


def test_the_dnu_prefix_does_not_hide_a_conviction():
    """ICOS prefixes some dispositions DNU-. It is not part of the outcome."""
    assert crs.get_dominant_charge(
        _charge('DNU-DISMISSED', 'DNU-GUILTY'))['disposition'] == 'GTR'


def test_a_count_with_no_disposition_does_not_outrank_one_with_a_disposition():
    """Seen on a real felony case: a blank alongside a dismissal and a guilty."""
    assert crs.get_dominant_charge(
        _charge('', 'DNU-DISMISSED', 'DNU-GUILTY'))['disposition'] == 'GTR'


def test_a_disposition_icos_uses_that_we_do_not_map_reads_as_other():
    """CLOSED is real and is deliberately not in the map.

    It arrived with CHANGE OF VENUE in the same run on 3 August 2026. That one
    could be read: the charge went to another county, so this record carries no
    outcome, and it is mapped. CLOSED cannot. A case closes after a conviction
    and after a dismissal alike, and guessing which would put a number in a
    bankruptcy column on the strength of a word that does not say. So it stays
    OTH, which is the code that means Napier does not know, and it stays
    something the run emails out while it is happening.
    """
    assert crs.get_dominant_charge(_charge('CLOSED'))['disposition'] == 'OTH'


def test_a_charge_moved_to_another_county_carries_no_outcome():
    """CHANGE OF VENUE, from the same run. The charge was decided elsewhere."""
    assert crs.get_dominant_charge(
        _charge('CHANGE OF VENUE'))['disposition'] == 'TNSF'
    assert 'TNSF' in case_parser.NOT_ADJUDICATED


def test_a_case_with_no_charges_is_not_a_criminal_case():
    assert crs.get_dominant_charge([]) is None


def test_the_answer_does_not_change_when_you_ask_twice():
    """It used to overwrite the caller's disposition list with its own answer.

    One pass through the workbook hid it. Anything that builds a CRS twice
    from the same parsed cases got 'GTR' back the first time and 'OTH' the
    second, because it then walked the string G, T, R as three dispositions.
    """
    charge = _charge('DISMISSED BY COURT', 'GUILTY - NEGOTIATED/VOLUN PLEA')
    first = crs.get_dominant_charge(charge)['disposition']
    second = crs.get_dominant_charge(charge)['disposition']
    assert first == second == 'GPL'


def test_the_charge_it_was_handed_comes_back_untouched():
    charge = _charge('DNU-DISMISSED', 'DNU-GUILTY')
    crs.get_dominant_charge(charge)
    assert charge[0]['disposition'] == ['DNU-DISMISSED', 'DNU-GUILTY']


def test_working_out_a_disposition_does_not_write_to_the_log():
    """Six debug lines a case buried real failures in the Heroku log."""
    noise = io.StringIO()
    with redirect_stdout(noise):
        crs.get_dominant_charge(_charge('DNU-DISMISSED', 'DNU-GUILTY'))
    assert noise.getvalue() == ''
