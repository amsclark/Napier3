"""CRS Lite has no formula for JWV or CIV, and Napier writes both.

The templates are not the same vocabulary. Every code in the full CRS is named
by some formula on some sheet; CRS Lite names neither JWV nor CIV anywhere at
all. A Lite workbook carrying one of those rows counted it on no sheet and said
nothing about it, so the file read as complete.

Both codes are live. CIV has come out of every extradition hold and cleared
parole violation since 19 August, and JWV since Iowa Legal Aid settled the
juvenile transfer wording on 20 August.

The workbook now says so in column V on the row itself, rather than the run
refusing to build or the code being quietly swapped for one the template
happens to know. Swapping would be worse: JUV is the nearest code Lite scores
and it means the juvenile court kept the case, which is the opposite of a
waiver up to adult court.

Synthetic case numbers throughout. The repository is public.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crs

FULL = 'CRS 3.5.5.xlsx'
LITE = 'CRS Lite 3.5.5.xlsx'


class FakeSheet(dict):
    """Just enough sheet for note_unscored_codes: column G in, column V out."""

    class Cell:
        def __init__(self, value=None):
            self.value = value

    def __init__(self, codes):
        super().__init__()
        for offset, code in enumerate(codes):
            self['G' + str(crs.FIRST_CASE_ROW + offset)] = self.Cell(code)

    def __getitem__(self, key):
        return self.setdefault(key, FakeSheet.Cell())

    def note_on(self, offset):
        return self['V' + str(crs.FIRST_CASE_ROW + offset)].value


def _notes(template, codes):
    sheet = FakeSheet(codes)
    crs.note_unscored_codes(template, sheet,
                            crs.FIRST_CASE_ROW + len(codes) - 1)
    return [sheet.note_on(i) for i in range(len(codes))]


class TestWhatEachTemplateScores:
    def test_the_full_crs_scores_everything_napier_writes(self):
        """Except OTH, which is meant to be in no formula in either file."""
        scored = crs.codes_the_template_scores(FULL)
        produced = set()
        for entry in crs.charge_code_map.values():
            produced.update(entry)
        produced.update(*crs.JUVENILE_DISPOSITIONS.values())
        assert produced - scored == set(crs.DELIBERATELY_UNSCORED)

    def test_lite_is_missing_jwv_and_civ(self):
        """The premise. If a future template fixes this, delete the guard."""
        scored = crs.codes_the_template_scores(LITE)
        assert 'JWV' not in scored
        assert 'CIV' not in scored

    def test_lite_does_score_the_ordinary_ones(self):
        scored = crs.codes_the_template_scores(LITE)
        for code in ('GTR', 'GPL', 'DEF', 'JUV', 'DISM', 'ACQ', 'WTHD',
                     'NOTF', 'TNSF'):
            assert code in scored, code


class TestTheNoteOnALiteWorkbook:
    @pytest.mark.parametrize('code', ['JWV', 'CIV'])
    def test_an_unscored_code_says_so(self, code):
        note = _notes(LITE, [code])[0]
        assert note == crs.UNSCORED_CODE_NOTE % code
        assert code in note

    def test_a_scored_code_says_nothing(self):
        assert _notes(LITE, ['GTR', 'JUV', 'DISM']) == [None, None, None]

    def test_only_the_row_that_carries_it_is_marked(self):
        assert _notes(LITE, ['GTR', 'JWV', 'DISM']) == [
            None, crs.UNSCORED_CODE_NOTE % 'JWV', None]

    def test_oth_is_left_alone(self):
        """OTH is in no formula on purpose, and the row already carries a
        column V note saying the disposition was not recognised. Marking it
        again would put the note on every uncoded row in every workbook."""
        assert _notes(LITE, ['OTH']) == [None]

    def test_an_empty_code_is_left_alone(self):
        """A blank column G is an open charge, which three sheets read on
        purpose."""
        assert _notes(LITE, [None, '']) == [None, None]

    def test_it_joins_a_note_already_there(self):
        sheet = FakeSheet(['JWV'])
        crs.append_note(sheet, crs.FIRST_CASE_ROW, 'Filed under TEST NAME.')
        crs.note_unscored_codes(LITE, sheet, crs.FIRST_CASE_ROW)
        note = sheet.note_on(0)
        assert note.startswith('Filed under TEST NAME.')
        assert 'JWV' in note


class TestTheFullCrsIsSilent:
    @pytest.mark.parametrize('code', ['JWV', 'CIV', 'GTR', 'TNSF', 'OTH'])
    def test_nothing_is_marked(self, code):
        assert _notes(FULL, [code]) == [None]
