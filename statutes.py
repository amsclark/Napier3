"""Split the statutory references into the helper columns CASE DATA hides.

CASE DATA column F holds every statute a case was adjudicated under, joined
with semicolons, and Napier writes it. Columns W to AH are twelve unheadered
columns off the right hand edge that take that string back apart, one code per
column, because the expungement sheet has to test the codes one at a time.

The template does the split with a CSE array formula, and that formula pads
every slot it did not fill with #VALUE!. That is its normal output, not a
failure: a case with one statute leaves eleven #VALUE! cells behind it.

Two columns of the expungement sheet read the range and only one of them
survives that:

    M   SUM(COUNTIF('CASE DATA'!W9:AH9,'CODE SECTIONS'!$V$2:$V$218&"*"))
        the ineligible-offence list. COUNTIF steps over errors, so M answers.

    N   OR(LEFT('CASE DATA'!W9:AH9,3)="719", ... )
        chapters 717C, 719, 720, 724, 726, 728 and 901A, the offences 901C.2
        will not seal. LEFT does not step over errors, so N is #VALUE!.

N is gated on M being "YES" and Excel stops at the first false test, so N is
only ever evaluated on a case that cleared the first screen. Which means the
910.7 half of the sheet fails on exactly the rows it exists to answer, and
nowhere else. Measured on the 210 captured cases: the 17 rows where M says YES
are precisely the 17 rows where N says #VALUE!, no exceptions in either
direction, and all five columns downstream of N go with it. Those 17 are
forgery, third degree theft, drug possession, assault, harassment, fugitive
from justice: the reason a client is at the clinic.

So Napier does the split itself and writes plain text, with an empty string in
the slots it does not fill. COUNTIF cannot tell an empty cell from an error, so
M keeps every answer it already gave. LEFT can, so N starts answering.

This is a mechanical resplit of a string Napier authored two columns earlier.
It decides nothing and it reads nothing back. The sheet author's tests are the
tests that run.

It also lifts a ceiling nobody would have found: both templates had the array
formula dragged to row 300, so case 298 onwards had no split at all.
"""

# W through AH. Twelve slots against a worst case of two statutes on one case
# across the whole captured corpus, so the overflow path below is a backstop
# rather than something that happens.
FIRST_COLUMN = 23
LAST_COLUMN = 34
SLOTS = LAST_COLUMN - FIRST_COLUMN + 1

FIRST_CASE_ROW = 4
STATUTE_COLUMN = 6          # F, the joined references
ROW_CAP = 5000

# Row 9 of both templates was dragged one column too far and carries a
# thirteenth slot in AI. Nothing reads it, the expungement sheet asks for
# W9:AH9, but it is the same array formula and it prints the same #VALUE! on a
# real case row. Anything out here holding the splitter is that mistake, and
# only the splitter is touched.
STRAY_COLUMNS = 12
SPLITTER_MARK = "'CASE DATA'!$F"

OVERFLOW_NOTE = (
    "This case is adjudicated under more than %d statutes and the expungement "
    "sheet only screens the first %d. The rest (%%s) have to be checked by "
    "hand against 901C.3." % (SLOTS, SLOTS))


def split_codes(reference):
    """Column F back into its parts, the same way the array formula split it.

    Empty fields are kept in place, because '715A.2(A);' is one code and a
    trailing empty slot and the sheet reads them by position.
    """
    if reference in (None, ''):
        return []
    return [part.strip() for part in str(reference).split(';')]


def _is_splitter(value):
    text = getattr(value, 'text', value)
    return isinstance(text, str) and text.startswith('=') and \
        SPLITTER_MARK in text


def _clear_strays(sheet, row):
    """Blank a splitter cell that was dragged past the last slot the sheet reads."""
    for column in range(LAST_COLUMN + 1, LAST_COLUMN + 1 + STRAY_COLUMNS):
        cell = sheet.cell(row=row, column=column)
        if _is_splitter(cell.value):
            cell.value = ''


def _template_depth(sheet):
    """How far down the template's own splitter was filled.

    Anything it wrote and Napier does not overwrite keeps its #VALUE! padding,
    which is only cosmetic on a row with no case but is the kind of thing a
    staffer reasonably reads as a broken workbook.
    """
    depth = FIRST_CASE_ROW - 1
    for row in range(FIRST_CASE_ROW, ROW_CAP + 1):
        if sheet.cell(row=row, column=FIRST_COLUMN).value is None:
            break
        depth = row
    return depth


def write_statute_split(workbook, case_count):
    """Fill W..AH from column F. Call once the case rows are written.

    Returns the rows whose statutes did not fit, which is empty for every case
    the corpus has seen.
    """
    sheet = workbook['CASE DATA']
    last_row = max(3 + case_count, _template_depth(sheet))
    if last_row < FIRST_CASE_ROW:
        return {}

    overflow = {}
    for row in range(FIRST_CASE_ROW, last_row + 1):
        codes = split_codes(sheet.cell(row=row, column=STATUTE_COLUMN).value)
        if len(codes) > SLOTS:
            overflow[row] = codes[SLOTS:]
            codes = codes[:SLOTS]
        for offset in range(SLOTS):
            sheet.cell(row=row, column=FIRST_COLUMN + offset).value = (
                codes[offset] if offset < len(codes) else '')
        _clear_strays(sheet, row)
    return overflow
