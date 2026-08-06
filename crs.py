import itertools
import re

from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from openpyxl.styles import Alignment # Added import
from openpyxl.styles import Font, PatternFill
from zoneinfo import ZoneInfo

# Iowa keeps one time zone, and this app runs on a dyno that keeps UTC. Every
# date in a workbook is a court date read against a clinic day: the twenty year
# cut on the SOL sheet, the two and eight year expungement waits, whether a
# probation term is still running. A run at nine at night in Des Moines was
# stamped tomorrow and measured every one of those waits a day early.
#
# Verified on the napier-dev dyno on 2026-08-01: date.today() answered
# 2026-08-02 while Iowa was still on the 1st.
IOWA = ZoneInfo('America/Chicago')


def iowa_now():
    """The moment, where the client and the courthouse are."""
    return datetime.now(IOWA)


def iowa_today():
    """The day in Iowa, which is the day the clinic is having."""
    return iowa_now().date()

MISMATCH_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
MISMATCH_FONT = Font(color="9C0006")

# Where the CRS template stops, counted in cases rather than rows. Napier writes
# CASE DATA from row 4 down and never stops, and Excel accepts every row it is
# given, so outgrowing the template costs a number rather than raising anything:
# the analysis sheets carry formulas only so far, and their totals sum a shorter
# range still. Nobody has hit these on a real client, but the failure is a
# quietly low figure carried into a hearing, so the run says something.
#
# tests/test_workbook_limits.py reads these back out of the two .xlsx files, so
# a new template that moves them fails the suite instead of going unnoticed.
FIRST_CASE_ROW = 4
# CASE DATA row 1 totals =SUM(x4:x300).
CASE_DATA_TOTAL_LIMIT = 297
# SOL, BANKRUPTCY and EXEMPTIONS each total =SUM(x4:x100). Full CRS only; the
# Lite template does not have those sheets.
ANALYSIS_TOTAL_LIMIT = 97
# The first sheet to run out of rows altogether: SOL's formulas stop at row 150.
ANALYSIS_ROW_LIMIT = 147
# In the Lite template that is EXPUNGEMENT & 910.7, whose rows are offset by one
# and stop at row 200, so its last case is CASE DATA row 201.
LITE_ANALYSIS_ROW_LIMIT = 198


def workbook_limits(written, is_lite):
    """What this many cases outgrows in the CRS template, worst first."""
    warnings = []
    if written > CASE_DATA_TOTAL_LIMIT:
        warnings.append(
            "CASE DATA's totals on row 1 only add up the first %d cases, so "
            "every total in this workbook is short."
            % CASE_DATA_TOTAL_LIMIT)
    row_limit = LITE_ANALYSIS_ROW_LIMIT if is_lite else ANALYSIS_ROW_LIMIT
    if written > row_limit:
        warnings.append(
            "The analysis sheets have rows for the first %d cases only, so the "
            "cases after that are on CASE DATA and nowhere else."
            % row_limit)
    if not is_lite and written > ANALYSIS_TOTAL_LIMIT:
        warnings.append(
            "The totals on SOL, BANKRUPTCY and EXEMPTIONS only add up the "
            "first %d cases. The rows below that are right, the totals are "
            "low." % ANALYSIS_TOTAL_LIMIT)
    return warnings

charge_code_map = {
    "GUILTY": {"GTR":1},
    "GUILTY BY COURT": {"GTR":1},
    "GUILTY - NEGOTIATED/VOLUN PLEA": {"GPL":1},
    "CONVERT TO SIMPLE MISDEM": {"GPL":1},
    "ACQUITTED": {"ACQ":0},
    "DISMISSED": {"DISM":0},
    "DISMISSED BY COURT": {"DISM":0},
    "DISMISSED BY OTHER": {"DISM":0},
    "DEFERRED": {"DEF":2},
    "NOT GUILTY": {"ACQ":0},
    "WAIVED TO ADULT COURT": {"JWV":0},
    "ADJUDICATED": {"JUV":1},
    "WITHDRAWN": {"WTHD":0},
    "NOT FILED": {"NOTF":0},
    "CIVIL": {"CIV":0},
    # A real run on 3 August 2026 alerted on this wording, which nothing in
    # 300 captured cases had shown. The charge moved to another county and was
    # decided there, so this record carries no outcome, and TNSF is the code
    # the expungement sheet has always cleared without anything ever producing
    # it. Ranked with the other non-convictions, so a case that also carries a
    # guilty count still reads as guilty.
    "CHANGE OF VENUE": {"TNSF":0},
    # Surfaced by widening the corpus to 400 cases across 68 counties. One
    # case carries it on both counts, two simple misdemeanours with $640.00
    # still owed, and it was falling through to OTH.
    #
    # OTH is the code that means Napier does not know, and the licence sheet
    # reads it as no conviction, so the sheet was answering "neither licence
    # nor registration" on a client who has a conviction and a remedy.
    #
    # GTR rather than GPL, and the choice does not matter: 299 formulas in the
    # template name both, and not one names either without the other. So no
    # computed answer anywhere in the workbook turns on whether the guilty
    # finding came from a trial or a plea, which is the only thing "- OTHER"
    # leaves open. What it does not leave open is that the client was found
    # guilty, and that is what every sheet actually asks.
    "GUILTY - OTHER": {"GTR":1}
}

# The rank for a disposition string charge_code_map has never seen. It sits
# between a dismissal and a conviction on purpose.
#
# It used to be 3, above everything else here. So one unrecognised word on one
# count of a case demoted the whole case to OTH, and OTH is not GTR, GPL or DEF,
# which is the test the licence sheet runs in 299 formulas and the expungement
# sheet in 396. A client with a conviction and one stray code came out of both
# looking like a client with no conviction at all, and the workbook gave no sign
# of it.
#
# Below a conviction now, so an unknown code cannot talk over one Napier
# understands. Still above a dismissal, so a case whose counts are all
# unrecognised reads as OTH rather than passing itself off as dismissed. Both
# are guesses, which is why an unrecognised code is also named on its own row
# and mailed out while the run is happening instead of being absorbed quietly.
#
# The two money sheets ask the question the other way round. BANKRUPTCY in 792
# formulas and EXEMPTIONS in 394 list the codes that mean no conviction and
# treat everything else as one, and OTH is not on that list either. So an
# unreadable disposition comes out of the workbook as no conviction on two
# sheets and as a conviction on the other two. That is the safer way for it to
# be wrong, because the alternative is telling a client that debt is
# dischargeable on the strength of a word nobody could read, but it does mean
# the note below cannot claim the sheets agree.
OTH_RANK = 0.5

# What "ADJUDICATED" is worth on a case whose number is not JVJV.
#
# The word is supposed to be the juvenile court's, and charge_code_map maps it
# to JUV at the rank of a conviction, which is right on a JVJV case. Iowa Legal
# Aid says the clerks also enter it on probation violation and contempt counts,
# and there it is not the case's disposition at all: the disposition is the
# conviction that put the client on probation in the first place.
#
# At equal rank the adjudication was winning. Two of the three captured cases
# carrying an ADJUDICATED count are felonies that also carry a negotiated plea,
# and both came out as JUV. Iowa Legal Aid switches those by hand today, on the
# old Napier as well as this one.
#
# It is not only column G. The count that wins also dates column D, so the row
# carried the day the probation violation was found rather than the day of the
# conviction, which is the other half of what they reported. And BANKRUPTCY and
# EXEMPTIONS both list JUV among the codes that mean no conviction, so a felony
# reading JUV came out of two sheets with its debt marked dischargeable.
#
# So on anything but a JVJV, an adjudication now loses to any real conviction.
# It stays above OTH rather than being struck out, because a case whose only
# disposition is an adjudication has to say something, and refusing to name it
# would render as "open charge" on three sheets, which is worse than a code with
# a caveat next to it. Nothing in the 300 captured cases takes that path: there
# is no non-JVJV case where an adjudication is the only disposition. If one
# turns up, ADULT_ADJUDICATION_NOTE says so in column V.
JUV_RANK_ADULT_CASE = 0.75

# What column V says about that row. The workbook outlives the alert and gets
# read by whoever has the client in front of them, so the guess has to be
# visible in the file itself and not only in Alex's inbox.
UNKNOWN_DISPOSITION_NOTE = (
    "Iowa Courts recorded a disposition Napier does not recognise (%s), so this "
    "case is coded OTH, and the sheets do not agree on what that means. The "
    "licence sheet reads OTH as no conviction and the expungement sheet answers "
    "n/a, but the bankruptcy and exemption sheets sort this case's debt as a "
    "conviction's. Check this case in ICOS before relying on any of them."
)

# The note above is for a count whose adjudication wording could not be read.
# That row is still coded, as OTH, and the note describes what OTH does.
#
# A case where no count has been adjudicated at all is a different row. The only
# wording it carries is the status ICOS prints for the case as a whole, and
# case_level_code deliberately refuses to translate most of that vocabulary, so
# column G is left empty. Until this, both rows got the OTH note, which told the
# attorney the case was coded OTH and then described how each sheet reads OTH.
# None of that is true of an empty column G, and the difference is not academic:
# three sheets read an empty column G as an open charge.
UNCODED_CASE_STATUS_NOTE = (
    "No count on this case has been adjudicated in Iowa Courts. The only "
    "disposition it carries is the status of the case as a whole (%s), which "
    "Napier does not translate into a CRS code, so column G is empty. The "
    "BANKRUPTCY, EXEMPTIONS and SOL sheets all read an empty column G as "
    "\"open charge\", so this case appears on those three as a charge still "
    "pending against the client. A case Iowa Courts has closed or transferred "
    "is not that. Check this case in ICOS before relying on any of them."
)


# Column H of CASE DATA, headed "Vehicular?". LICENSE-REGIS reads it in 299
# formulas and it is the whole of the difference between the two answers that
# sheet can give: convicted with debt and H="YES" is "License & registration",
# anything else is "Registration only". Napier never wrote the column, so the
# sheet has never once said "License & registration" about anybody. Blank is not
# neutral there, it is a quiet "no".
#
# Iowa suspends a driver's licence for unpaid debt on a chapter 321 conviction
# and holds vehicle registration for delinquent court debt of any kind, which is
# the distinction the sheet is drawing. So the test is the chapter the statute
# sits in, which Napier already has: it is the adjudicated statutory code in
# column F.
# A city or county prosecuting a traffic offence under its own ordinance puts
# the ordinance citation in the adjudicated code, and ICOS prints that instead of
# the state section. Nine of 90 captured cases are charged that way and every one
# of them is a motor vehicle matter: speeding, seat belts, parking, driving an
# unregistered vehicle. The chapter test used to anchor at the start of the
# string, so all nine read as not a chapter 321 matter at all.
#
# They come in two shapes and only one of them is answerable.
#
# DU/32-321.285(d)(3) is Dubuque adopting the state speeding section by number,
# and the state section is right there in the code. So the chapter is looked for
# at the start of the code or straight after an ordinance citation's / or -,
# which finds it in both shapes and still will not match a chapter that merely
# ends in those digits.
VEHICULAR_CHAPTER = re.compile(r'(?:^\s*|[-/])321[A-Z]?\.', re.I)
# MA/62.01(120)-0198 is the other shape: a bare municipal code section. 62.01 in
# one city's code has nothing to do with 62.01 in another's and neither has
# anything to do with the state chapters, so there is no chapter in it to read.
ORDINANCE_CITATION = re.compile(r'^[A-Z]{2}/', re.I)
# Homicide and serious injury by vehicle live in chapter 707 rather than 321 and
# revoke a licence on conviction, so they are vehicular for this purpose even
# though the chapter does not say so.
VEHICULAR_SECTIONS = ('707.6A',)

# Iowa Code 123.46(6) and 123.47(9) let a public intoxication or PAULA
# conviction be expunged two years on, and 725.1(4) does the same for
# prostitution. The EXPUNGEMENT sheet has a column for each, and both used to
# ask whether column F was the string "123.46", "123.47" or "725.1" exactly.
# Iowa Courts never write a section that bare. Across 300 captured cases the two
# that carry one of these statutes read "123.47(4)" and "PO/123.47(2)", so both
# columns answered NO on every case ever run and the two year clock they exist
# to start was never mentioned to anybody.
PUBLIC_INTOX_PAULA_SECTIONS = ('123.46', '123.47')
PROSTITUTION_SECTIONS = ('725.1',)

# The three codes LICENSE-REGIS treats as a conviction. On anything else it says
# "Z - Neither license nor registration" and never looks at column H at all.
LICENCE_DISPOSITIONS = frozenset({'GTR', 'GPL', 'DEF'})

# Leaving column H blank is honest but it is not visible: LICENSE-REGIS reads a
# blank exactly as it reads "NO" and prints "Registration only", which is a
# sentence about the client's driving licence that nobody checked. So on the rows
# where that sentence actually gets printed, the row says why.
ORDINANCE_VEHICULAR_NOTE = (
    "Column H is blank because Iowa Courts give the adjudicated charge as %s, a "
    "city or county ordinance citation with no state chapter in it, so Napier "
    "cannot tell whether this is a chapter 321 offence. LICENSE-REGIS reads the "
    "blank as \"Registration only\". If the ordinance mirrors a chapter 321 "
    "offence the licence is at stake too, so check this one before advising on "
    "it."
)

# The row has one disposition date and the case had several. Column D now holds
# the one that goes with the code in column G, which is the pairing the sheets
# assume, but the SOL sheet runs its twenty year test off that single date and
# will apply it to every dollar on the row including debt from a count disposed
# years earlier.
DISPOSITION_SPREAD_NOTE = (
    "Counts on this case were disposed on different dates (%s). Column G is the "
    "disposition code and column D is a date: %s, the day the count behind that "
    "code was disposed. The SOL sheet applies its 20 year test to that one date "
    "for the whole row, so if this case is near the 20 year line check the "
    "counts separately."
)

# The wording above used to be "Column D holds X, the date of the disposition in
# column G", which Iowa Legal Aid read as a claim that column G holds a date.
# It does not and never did. Both halves are named now.

# What column V says when a case that is not a JVJV still comes out as JUV. See
# JUV_RANK_ADULT_CASE for why that is now rare and why it is still possible.
ADULT_ADJUDICATION_NOTE = (
    "The only disposition Iowa Courts show on this case is an adjudication, and "
    "this is not a juvenile case number, so column G reads JUV. Clerks enter "
    "\"Adjudicated\" on probation violation and contempt counts as well as on "
    "juvenile ones. If that is what this is, the case's real disposition is not "
    "on the page and JUV is wrong: BANKRUPTCY and EXEMPTIONS both read JUV as "
    "no conviction and will treat this debt as dischargeable. Check it."
)

# Column D is blank on a case no court has ruled on yet, and it has to stay
# blank: the EXPUNGEMENT sheet counts blank-D rows as the pending charges that
# block expungement under 901C.2. But the SOL sheet reads the same cell
# arithmetically, and a blank reads as zero, which is a date in 1900.
PENDING_CASE_NOTE = (
    "Iowa Courts show no adjudication on this case, so column D is blank and "
    "column G has no code. Column D blank is what tells the EXPUNGEMENT sheet "
    "this charge is still pending, but the SOL sheet reads it as a date and a "
    "blank reads as 1900: it reports this row's indigent defense and collection "
    "costs as barred by the 20 year limit, counts jail and room & board as 20 "
    "years old, and leaves the rest of the balance out of all three of its "
    "columns. Nothing here has aged out, because there is no judgment yet. "
    "Treat this row's SOL figures as unanswered."
)


def is_vehicular(statutes):
    """"YES", "NO", or None when there is nothing to judge from.

    None matters. A civil case writes "n/a" into column F and a case whose only
    counts were dismissed writes nothing at all, and in neither is there a
    conviction for a licence to hang off. Saying "NO" there would be asserting
    something Napier does not know, and the sheet reads a blank and a "NO"
    identically anyway, so the honest answer costs nothing.

    A bare municipal ordinance citation is the same situation and used to get a
    confident "NO". It is a section number in one city's code and says nothing
    about which state chapter, if any, the offence answers to.

    Any vehicular count carries the case. A client who pleaded to OWI and a
    drugs count has a licence problem regardless of which count the CRS picked
    to speak for the case in column G.
    """
    if not statutes:
        return None
    codes = [code.strip() for code in str(statutes).split(';')]
    codes = [code for code in codes if code and code.lower() != 'n/a']
    if not codes:
        return None
    for code in codes:
        if VEHICULAR_CHAPTER.search(code):
            return "YES"
        if any(code.upper().startswith(section) for section in VEHICULAR_SECTIONS):
            return "YES"
    if any(ORDINANCE_CITATION.match(code) for code in codes):
        return None
    return "NO"


def cites_section(statutes, sections):
    """"YES", "NO", or None: does any adjudicated count answer to one of these?

    Same contract and the same three answers as is_vehicular, and for the same
    reasons. A civil case's "n/a" and a case with no adjudicated statute give
    None rather than a confident "NO", and so does a bare municipal ordinance
    citation, which is a section number in one city's code with no state section
    in it to compare.

    A section is looked for at the start of a code or straight after an
    ordinance citation's / or -, which is where is_vehicular looks for chapter
    321 and finds it in PO/123.47(2) as readily as in 123.47(4). It has to be
    followed by the end of the code or by something that is not another digit,
    so 725.1 does not match 725.10 and 123.46 does not match 123.460.

    Any count carries the case, because the client has the conviction whichever
    count get_dominant_charge picked to speak for the case in column G.
    """
    if not statutes:
        return None
    codes = [code.strip() for code in str(statutes).split(';')]
    codes = [code for code in codes if code and code.lower() != 'n/a']
    if not codes:
        return None
    for section in sections:
        pattern = re.compile(r'(?:^\s*|[-/])%s(?![\d.])' % re.escape(section),
                             re.I)
        if any(pattern.search(code) for code in codes):
            return "YES"
    if any(ORDINANCE_CITATION.match(code) for code in codes):
        return None
    return "NO"


# Two statutes that are not crimes. Fugitive from justice is an extradition
# hold and violation of parole is an executive revocation, so neither is a
# conviction the client carries, and Iowa Legal Aid asked for both to read as
# civil. Napier codes off whatever the clerk typed in the disposition, so the
# four captured fugitive cases came out under four different codes: GTR, TNSF,
# WTHD and OTH. All five owe nothing, so no money moves today.
#
# Exactly these two, never chapter 908. Violation of probation is 908.11, one
# section over, and the same 538 cases carry 20 of those worth about $17,600,
# most of them real guilty pleas. A chapter rule would flip all of them to
# civil, which is the opposite of what Iowa Legal Aid asked for the last time
# this came up. cites_section is what keeps them apart: it will not match 908.1
# against 908.11 because it refuses a trailing digit.
CIVIL_SECTIONS = ('820.2', '908.1')

# What a clerk files a pre-electronic-docket case under. Either spelling is
# enough on its own, because the captured case carries both and there is no
# reading of one without the other that means anything else.
OLD_CASE_CODE = 'CR/OLDCASE'
OLD_CASE_DESCRIPTION = 'OLD CASE CHARGE CODE'


def is_old_case_code(charge):
    """Is this the clerk's placeholder for a case whose file is on paper?"""
    code = (charge.get('original_charge') or charge.get('charge') or '')
    description = (charge.get('original_description')
                   or charge.get('description') or '')
    return (code.strip().upper() == OLD_CASE_CODE
            or description.strip().upper() == OLD_CASE_DESCRIPTION)


def only_civil_sections(statutes):
    """True when every adjudicated count on the case is one of CIVIL_SECTIONS.

    Every count, not any count. A case holding someone as a fugitive alongside
    a real conviction is a case with a real conviction, and Iowa Legal Aid was
    explicit that the conviction wins. Recoding the whole row CIV on the
    strength of one non-criminal count would move the conviction's balance into
    the dischargeable and exempt columns on the bankruptcy and exemption
    sheets, which is a worse error than the label it fixes.
    """
    if not statutes:
        return False
    codes = [code.strip() for code in str(statutes).split(';')]
    codes = [code for code in codes if code and code.lower() != 'n/a']
    if not codes:
        return False
    return all(cites_section(code, CIVIL_SECTIONS) == "YES" for code in codes)


# How far back to look when working out what somebody is paying now. A court
# asking whether a person can pay wants the recent record, not an average that
# a garnishment in 2003 drags upwards.
RECENT_MONTHS = 12


def _money(text):
    """A dollar figure off an ICOS page, or None when there is not one."""
    if text is None:
        return None
    text = str(text).replace('$', '').replace(',', '').strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


# The line a civil money judgment sits on in the same itemization table as the
# court fees. It is what one private party owes another, not a fee owed to the
# clerk, and it is settled by whatever the parties do rather than by the client
# paying the court. ICOS records it satisfied with a journal entry, so it looks
# from the table exactly like a fee that has been paid.
#
# The match has to be the whole line and not the word. CONFESSION OF JUDGMENT -
# $5000 OR MORE A-R is the fee for filing one, and DEFERRED JUDGMENT CIVIL
# PENALTY is a fine that get_finance_column already sends to column R. Both are
# court debt and both carry the word.
JUDGMENT_LINE = 'JUDGMENTS'


def is_judgment(detail):
    """Whether an itemization line is the judgment itself rather than a fee."""
    return (detail or '').strip().upper() == JUDGMENT_LINE


# Money sitting with the clerk on somebody's behalf, rather than money applied
# to what the client owes. A defendant, or more often somebody standing behind
# them, puts up an appearance bond, and the clerk holds it and later gives it
# back or applies it. Either way the line records the money moving, not the
# defendant paying down their debt. Where it is applied, ICOS bills the fees it
# covered on their own lines and marks those paid, so counting the deposit as
# well counts the same money twice.
#
# Every one of these in the captured corpus is assessed and paid at the same
# amount, so none of them contributes anything to what ICOS says is owed, and
# taking them out of the payment history cannot put Napier's arithmetic at odds
# with the court's. That is the test to apply to any wording added here.
#
# Whole lines again, for the reason JUDGMENT_LINE is. A bond assignment fee or a
# forfeiture is court debt and carries the word, and forfeited bond money is
# money the county keeps rather than money that comes back. REFUNDABLE is a
# whole line for a second reason: REFUNDABLES DUE TO PREPAID EXPENSES is a
# different thing on civil cases and is deliberately still counted, for the
# reasons in OPEN_QUESTIONS.md.
DEPOSIT_LINES = ('APPEARANCE BOND REFUND', 'BONDS - ESCROW', 'REFUNDABLE')


def is_clerk_deposit(detail):
    """Whether a line is money held for the client rather than money paid."""
    return (detail or '').strip().upper() in DEPOSIT_LINES


def payments(case):
    """Every payment ICOS records on a case, oldest first.

    The itemization has carried a date, a receipt number and a tender type
    against each paid line for as long as Napier has been fetching it, and
    nothing has ever read them. A fee paid in instalments gets a row per
    instalment, so this is a payment history rather than a list of fees.

    Third-party collection fees are excluded the same way they are excluded
    from the fee columns: ICOS lists them and does not count them in the case
    total, so counting a payment against one as money paid on the case would
    overstate what the client has actually put in.

    A satisfied civil judgment is excluded for the same reason and at a very
    different scale. Across the 210 captured cases ten judgment lines accounted
    for $4,024,095.44 of the $4,045,517.55 this function reported, which is
    99.5% of it, against $16,602.57 of actual court debt. One of them was a
    single judgment listed six times, once per debtor. What is left after
    taking them out is $21,422.11 across 488 payments, which is a person paying
    the clerk.

    Money the clerk is holding is excluded for the third time on the same
    grounds. It is the one that reaches criminal cases, which is what most of
    this workbook is for, and it lands on a single client rather than washing
    out across a pooled figure. One captured case owes nothing and has a $4,000
    escrowed bond as its only line, and this reported it as $4,000 paid and
    $333.33 a month. Another owes nothing, has a $2,490 REFUNDABLE against $155
    of real fees, and reported $2,645 a month. Those monthly figures are what an
    ability-to-pay argument is built out of, so the error runs against the
    client every time.
    """
    history = []
    last_detail = None
    for row in case.get('financials') or []:
        detail = (row.get('detail') or '').strip()
        if detail:
            last_detail = detail
        line = detail or last_detail or ''
        if (is_excluded_fee(line) or is_judgment(line)
                or is_clerk_deposit(line)):
            continue
        paid = _money(row.get('paid'))
        when = parse_us_date(row.get('paidDate'))
        if paid is None or paid <= 0 or when is None:
            continue
        history.append({
            'date': when,
            'amount': paid,
            'receipt': row.get('receipt'),
            'tender': row.get('tender'),
            'detail': detail or last_detail,
        })
    history.sort(key=lambda payment: payment['date'])
    return history


def judgments(case):
    """The civil judgments on a case, so taking them out of the payment history
    does not throw them away.

    A judgment against the client is the largest number anywhere near them and
    it decides whether they are being garnished, which is the first thing an
    ability-to-pay argument runs into. It is not court debt and it does not
    belong in the court debt figures, but it does belong on the page.

    ICOS lists the same judgment once per debtor, so a judgment against six
    people is six rows with six receipt numbers and one amount between them.
    The captured corpus has exactly that: one judgment a little over $650,000
    listed six times on one case, all on the same day. Rows agreeing on both
    the amount and the date are treated as one judgment, because adding them up
    is how that judgment came out six times its own size.
    """
    found = []
    for row in case.get('financials') or []:
        if not is_judgment(row.get('detail')):
            continue
        amount = _money(row.get('amount'))
        if amount is None or amount <= 0:
            continue
        when = parse_us_date(row.get('paidDate'))
        if any(seen['amount'] == amount and seen['date'] == when
               for seen in found):
            continue
        found.append({
            'amount': amount,
            'satisfied': _money(row.get('paid')) or Decimal(0),
            'date': when,
            'receipt': row.get('receipt'),
        })
    return found


def payment_history(case, as_of):
    """What a case's payment record says, or None when there is no record.

    None and a record of zero payments are different answers and the sheet
    should not have to guess which it is looking at. A case ICOS has no
    itemization for cannot tell us the client never paid.
    """
    history = payments(case)
    if not history:
        return None
    total = sum((payment['amount'] for payment in history), Decimal(0))
    first, last = history[0]['date'], history[-1]['date']
    recent = sum((payment['amount'] for payment in history
                  if payment['date'] > _add_term(as_of, -RECENT_MONTHS, 'Month')),
                 Decimal(0))
    # Over the window the client was actually paying, not over the age of the
    # case. Somebody who paid steadily for a year and then lost the job has a
    # monthly figure of what they paid, and the gap is reported separately.
    months = max(1, _months_between(first, last) + 1)
    return {
        'count': len(history),
        'total': total,
        'first': first,
        'last': last,
        'monthly': (total / months).quantize(Decimal('0.01')),
        'recent': recent,
        'recent_monthly': (recent / RECENT_MONTHS).quantize(Decimal('0.01')),
        'months_since_last': _months_between(last, as_of),
        'tenders': sorted({payment['tender'] for payment in history
                           if payment['tender']}),
    }


def _months_between(start, end):
    """Whole months from start to end, never negative."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


# CASE DATA column I, "Under supervison?" [sic], is deliberately not written
# here, and a future reader should know it was tried rather than overlooked.
#
# The charges page does carry a sentence table with the type, the date and the
# duration, so Napier can work out whether a probation term is still running on
# paper, and a build that did exactly that went to Iowa Legal Aid on 3 August
# 2026. They turned it down, and the reason is not one more parsing rule can
# fix: ICOS does not record an early discharge, records an extension only
# sometimes, and never carries parole at all, because parole is corrections
# rather than the court. Their words were that ICOS "is not going to be
# reliable because sometime they don't update if probation was pushed out or
# ended early", and that they establish this per client against the Department
# of Corrections website instead.
#
# A wrong YES here puts debt in the expungement sheet's 910.7 restitution
# column, so the column stays with the staff who can answer it. It reads as
# "n/a" when blank, which is what it has always read and is the honest answer.
#
# case_parser still records the sentence table on the case, because that is a
# faithful reading of the page and costs nothing. Nothing acts on it.


def _add_term(start, count, unit):
    """The day a term of `count` `unit`s beginning on `start` runs out.

    Calendar arithmetic rather than a multiple of 365, because a five year
    probation term imposed on a leap day is not five times 365 days long and
    the answer here decides what goes in a column about somebody's debt.
    """
    unit = unit.lower()
    if unit == 'day':
        return start + timedelta(days=count)
    if unit == 'week':
        return start + timedelta(weeks=count)
    months = count * 12 if unit == 'year' else count
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # The 31st of a month landing on a 30 day month, and 29 February landing on
    # a common year, both fall back to that month's last day.
    day = min(start.day, monthrange(year, month)[1])
    return date(year, month, day)


def parse_us_date(text):
    """MM/DD/YYYY as ICOS writes it, or None for anything else."""
    if not text:
        return None
    try:
        return datetime.strptime(str(text).strip(), '%m/%d/%Y').date()
    except ValueError:
        return None


def case_type(case_id):
    """The four character docket type out of an ICOS case number, or ''.

    ICOS writes a case number as a five character county code, two spaces, then
    the type and the sequence: "00000  FECR000000". Everything that reads the
    type reads those same four characters, so the slice lives here rather than
    in each caller.
    """
    case_id = (case_id or '').strip()
    return case_id[7:11].upper() if len(case_id) >= 11 else ''


def is_juvenile_case(case_id):
    """Whether this docket is the juvenile court's.

    Iowa Legal Aid asked for JUV to be possible only on a "JVJV" case number.
    The whole JV family is accepted rather than that one type, because the
    juvenile docket also carries JVCV and JVDV numbers and the cost of being
    wrong runs one way: a genuine juvenile case that Napier failed to recognise
    would have its adjudication demoted, which is the outcome this is here to
    prevent. Nothing but JV reaches it.
    """
    return case_type(case_id).startswith('JV')


def get_dominant_charge(charges, case_id=None):
    """Pick the one disposition code that represents the whole case.

    ICOS lists a disposition per count, so a plea deal shows up as a guilty
    alongside several dismissals. The CRS has one column for it, and the
    ranking in charge_code_map decides which count speaks for the case.

    case_id is the ICOS case number, which carries the case type in characters
    7 to 11. The only thing read off it is whether this is a juvenile case, for
    JUV_RANK_ADULT_CASE. Omitting it reads as not juvenile, which is the way
    round that cannot invent a JUV on an adult case. process_case, the only
    caller that matters, always has the number and always passes it.

    Returns a copy. The caller's charge keeps its list of dispositions, so
    calling this twice on the same case gives the same answer both times.

    The copy carries 'unknown_dispositions', the ICOS wordings that produced an
    OTH. Empty on almost every case. When it is not, the case is coded on a
    guess and the caller is the one that has to say so.
    """
    if len(charges) == 0:
        return None
    delisted = dict(charges[0])
    raw_charge = delisted['disposition']
    raw_dates = delisted.get('disposition_dates') or []
    charge_dict = {}
    # Which counts produced each code, by date, so the winning code can be
    # paired with the date of the count that actually produced it.
    dates_by_code = {}
    unknown = set()
    for index, disposition in enumerate(raw_charge):
        disposition = disposition.replace("DNU-", "")
        if not disposition:
            # ICOS printed no adjudication for this count. That is the absence
            # of a disposition, and it used to be recorded as NOTF, NOT FILED,
            # which is a specific thing that was not true of any of the three
            # real cases it landed on: one filed eleven days earlier and still
            # open, one ICOS dismissed in 2021, one ICOS closed in 1993.
            #
            # It is not a harmless label. The EXPUNGEMENT sheet's DISM ACQ?
            # column reads NOTF as dismissed or acquitted and answers YES,
            # eligible under 901C.2, so an open charge was reported as
            # expungeable. Nothing is recorded here instead, which is the
            # workbook's own way of saying it: SOL, BANKRUPTCY and EXEMPTIONS
            # each render column G as IF(G=0, "open charge", G).
            continue
        elif disposition not in charge_code_map:
            charge_dict["OTH"] = OTH_RANK
            unknown.add(disposition)
            charge_key = "OTH"
        else:
            charge_key, rank = next(iter(charge_code_map[disposition].items()))
            if charge_key == "JUV" and not is_juvenile_case(case_id):
                rank = JUV_RANK_ADULT_CASE
            charge_dict[charge_key] = rank
        if index < len(raw_dates) and raw_dates[index]:
            dates_by_code.setdefault(charge_key, []).append(raw_dates[index])
    sorted_tuples = sorted(charge_dict.items(), reverse=True,
                           key=lambda item: item[1] if item[1] is not None else float('inf'))
    delisted['disposition'] = sorted_tuples[0][0] if sorted_tuples else ''
    delisted['unknown_dispositions'] = sorted(unknown)

    # Column D is the date of the disposition column G names, and on a case
    # where the counts were disposed on different days those two used to come
    # from different counts. The parser handed over whichever date came first on
    # the page and column G is chosen by rank, so a case pleaded out on one
    # count and adjudicated on another more than a year later reported the later
    # code against the earlier date.
    #
    # That pairing is not cosmetic. The SOL sheet asks IF(D+7300 < today) on
    # every row, the twenty year limit on enforcing an Iowa judgment, and it
    # sorts the row's debt into time barred or "NO ARGUMENT" on the answer.
    # Taking the earliest date available makes a judgment look older than it is,
    # so the error ran toward telling an attorney a debt had aged out when it
    # had not.
    #
    # Where several counts share the winning code, the last of them is the day
    # the case finished reaching that disposition, and it is also the reading
    # that will not retire a debt early.
    winning_dates = dates_by_code.get(delisted['disposition']) or []
    if winning_dates:
        delisted['dispositionDate'] = max(
            winning_dates, key=lambda d: parse_us_date(d) or date.min)

    # A row that compresses counts disposed on different days is saying less
    # than the case does, and the sheet that reads the date cannot see the
    # spread. Recorded here, said in column V by the caller.
    spread = sorted({d for d in raw_dates if d})
    delisted['disposition_date_spread'] = spread if len(spread) > 1 else []
    return delisted


def case_level_code(status):
    """The CRS code for ICOS's case-level status, or None if it is not one.

    The status ICOS prints on the case summary is its own vocabulary. Across 300
    captured pages it reads GUILTY PLEA/DEFAULT, VIOLATIONS HANDLED BY CLERK,
    DISMISSED, BY TRIAL TO COURT, CLOSED, OTHER JUDGMENT, TRANSFERRED, SMALL
    CLAIM-DISPOSED BY CLERK, DEFAULTED, DEFERRED JUDGEMENT, DISCHARGE and
    CONVERTED TO SIMPLE MISDEMEANR, and it overlaps the per-count adjudication
    wordings at DISMISSED and nowhere else. The last four appeared only after the
    corpus passed 90 pages, which is the reason for reading it this way: the
    vocabulary is still growing and a guess made now would be wrong later.

    Only the overlap is read. The rest are not translated here, because whether
    VIOLATIONS HANDLED BY CLERK is a guilty plea is a question about Iowa
    practice rather than about parsing, and five sheets key formulas on the
    answer. An unrecognised status returns None and travels out through
    unknown_dispositions, which already puts the wording in column V and tells
    the run, so the case is visibly uncoded rather than quietly miscoded.
    """
    entry = charge_code_map.get((status or "").replace("DNU-", "").strip())
    return next(iter(entry)) if entry else None


def get_finance_column(detail):
    # A county attorney collection fee is really a payment marker, so no column
    # is truly right for it. Across 538 captured cases it puts $0.00 of
    # assessed-and-unpaid money anywhere: 16 cases carry one, 49 rows between
    # them, and the clerk marked 47 of those paid with a receipt and a tender.
    # The other 2 sit on a case Napier already drops. So this moves nothing
    # today on any case anyone has measured.
    #
    # It is in K rather than P for the rare case that does carry a balance.
    # What it would be then is collection debt, and K is where the other two
    # collection fees go, which is the half of the bankruptcy sheet's J+K that
    # reads as dischargeable. P is UNKNOWN, and unknown money is covered by no
    # exemption and called not dischargeable, which is the wrong way for a
    # guess about a client's debt to run.
    if "COLLECTION BY CO ATTY" in detail:
        return "K" # COLLECTION FEE
    if "DELINQUENT REVOLVING FUND" in detail:
        return "P" # UNKNOWN

    # These four are read before the word FINE is looked for, and that order is
    # the whole point of them.
    #
    # A structured fine is Iowa's instalment arrangement, and a clerk itemizing
    # one writes each component fee with the arrangement's name attached:
    # "INDIGENT DEFENSE-STRUC FINES-REIMB STATE", "COURT REPORTER SERVICES
    # STRUC FINE", "TIME PAYMENT FEES-STRUCTURED FINES", "DOCKET PROC - STRUCT
    # FINE ABOVE SIMP". The trailing words say how the money is being
    # collected. The leading words say what it is, and what it is decides the
    # column. Matching on FINE anywhere in the string read all four as fines,
    # so a reimbursement to the state public defender came out in FINES, where
    # the expungement sheet cannot see it at all and the bankruptcy sheet calls
    # it not dischargeable.
    #
    # SURCH rather than SURCHARGE because ICOS abbreviates it when the wording
    # runs long, and "DOMESTIC/SEXUAL ABUSE, STALKING, HUMAN TRAFF VICTIM
    # SURCH" runs long. Missing it by those two letters put a victim surcharge
    # in MISCELLANEOUS, which the bankruptcy sheet calls possibly dischargeable
    # and the exemption sheet covers with every exemption, when a surcharge is
    # neither. Nothing in 400 captured cases contains SURCH and is not one.
    if "INDIGENT DEFENSE" in detail:
        return "J" # INDIGENT DEFENSE
    if "SURCH" in detail:
        return "Q" # SURCHARGE
    if "COURT REPORTER" in detail:
        return "O" # MISC, as the fee reads without the qualifier
    if "TIME PAYMENT" in detail or "DOCKET PROC" in detail:
        return "O" # MISC, as the fee reads without the qualifier

    if "FINE" in detail:
        return "R" # FINE
    if "DEFERRED JUDGMENT CIVIL PENALTY" in detail:
        return "R" # FINE
    if "INFRACTIONS-PENALTIES AND FORFEITURES-CITY" in detail:
        return "R" # FINE
    if "NONSCHEDULED CHAPTER 321" in detail:
        return "R" # FINE
    if "SCHEDULED VIOLATION/NON-SCHEDULED" in detail:
        return "R" # FINE
    
    #if "FILING" in detail:
    #    return "J" # FILING
    #if "COURT COSTS" in detail:
    #    return "J" # FILING
    #if "TRAFFIC/SIMP MISD APPEAL FEES" in detail:
    #    return "J" # FILING
    #if "OTHER SIMPLE MISDEMEANORS" in detail:
    #    return "J" # FILING

    if "ROOM/BOARD" in detail:
        return "L" # JAIL / ROOM & BOARD

    if "RESTITUTION" in detail:
        return "S" # RESTITUTION

    if "THIRD PARTY" in detail:
        return "K" # LINEBARGER COLLECTION FEE

    if "REVENUE" in detail:
        return "K" #DEPARTMENT OF REVENUE COLLECTION FEE

    if "SHERIFF" in detail:
        return "M" # SHERIFF

    if "PROBATION" in detail:
        return "N" # PROBATION REVOCATION FEE

    return "O" # MISC

ICOS_BUCKETS = ('COSTS', 'FINE', 'SURCHARGE', 'RESTITUTION', 'OTHER')

# Rows the summary counts as COSTS. Everything the summary does not put in one
# of the four named buckets falls into OTHER, so only COSTS needs listing.
COSTS_MARKERS = (
    'SHERIFF', 'INDIGENT DEFENSE', 'COURT COSTS', 'FILING', 'CLERK',
    'WITNESS', 'JURY', 'SERVICE', 'ROOM/BOARD', 'ATTORNEY FEE',
    'TRANSCRIPT', 'APPEAL FEES', 'DEPOSITION',
    # Both of these read like OTHER and are not. Polk County put $5.27 of
    # postage in COSTS and $37.00 of city/county misc fees in COSTS, in its own
    # summary, on pages where the itemization was otherwise exact. Guessing
    # wrong here is cheap and self-announcing: the bucket stops adding up and
    # the row falls back to category totals, which is where both of these
    # already were.
    'POSTAGE', 'MISC FEES BY CITY/COUNTY',
    # Same class as the two above, found the same way and carrying the same
    # safety net. 36 of the 400 captured cases fail the partition check, and on
    # 27 of them COSTS runs short of its summary while OTHER runs over by the
    # same amount to the cent, which is a fee filed under COSTS being read as
    # OTHER. On every one of the 27 the difference is exactly these wordings:
    # ten of them alone, and one Linn case where a $500.00 REFUNDABLE and a
    # $500.00 bond refund cover the $1,000.00 together. REFUNDABLE also covers
    # REFUNDABLES DUE TO PREPAID EXPENSES, which the overrides below already
    # file under COSTS, so held money reads consistently.
    'REFUNDABLE', 'APPEARANCE BOND REFUND', 'LIENS, ENTERING/ENDORSEMENT',
    'FINAL DECREE OF DISSOLUTION', 'COPY/BINDER FEES',
    'SCHEDULED VIOLATION REQU CT APPEAR', 'SMALL ESTATE ADMINISTRATION',
    'CONFESSION OF JUDGMENT', 'PRAECIPE', 'OTHER SIMPLE MISDEMEANORS',
)

FINE_MARKERS = (
    'FINE', 'DEFERRED JUDGMENT CIVIL PENALTY',
    'INFRACTIONS-PENALTIES AND FORFEITURES-CITY',
    'NONSCHEDULED CHAPTER 321', 'SCHEDULED VIOLATION/NON-SCHEDULED',
)

# Fees whose wording says one bucket and whose own ICOS summary says another.
# The markers above are a reading of the fee name; these are what the clerk
# actually filed the fee under, which is the only thing the reconciliation
# cares about, so they are checked first and they win.
#
# Every one of these was measured across 259 captured cases carrying both a
# summary and an itemization. Each was applied on its own and none of them cost
# a single bucket that was adding up before; together they take the buckets
# that reconcile from 555 of 674 to 600 of 655, and the rows where the whole
# itemization reconciles from 201 to 228 of 259.
#
# The stakes are not cosmetic. A bucket that does not add up used to surrender
# the fee breakdown for everything in it, so two $100 probation revocation fees
# filed under OTHER took a $60 indigent defense fee in the same case out of
# INDIGENT DEFENSE and into MISCELLANEOUS, which is the difference between
# having a 910.7 analysis and not having one.
SUMMARY_BUCKET_OVERRIDES = (
    # Filed under COSTS by the clerk, whatever the wording suggests.
    ('PARKING VIOLATION PER COMPLAINT', 'COSTS'),
    ('PROBATION REVOCATION FEE', 'COSTS'),
    ('REFUNDABLES DUE TO PREPAID EXPENSES', 'COSTS'),
    ('TITLE CHANGE REAL ESTATE', 'COSTS'),
    ('PROBATE ENTERING ORDER', 'COSTS'),
    ('CERTIFICATE AND SEAL (PROBATE)', 'COSTS'),
    ('PROBATE FEES PAID TO PRIVATE REFEREE', 'COSTS'),
    # These two carry fine wording and the clerk files them under COSTS. Only
    # the reconciliation moves; the column the client reads is still FINES,
    # which is a separate question this does not touch.
    ('SCHEDULED VIOLATION/NON-SCHEDULED', 'COSTS'),
    ('NONSCHEDULED CHAPTER 321', 'COSTS'),
    # The county attorney's collection fee is charged against the fine and the
    # summary counts it there, not in OTHER where its wording lands it.
    ('COLLECTION BY CO ATTY', 'FINE'),
    # A structured fine is Iowa's instalment arrangement, and a clerk itemizing
    # one writes each component fee with the arrangement's name attached. These
    # four are court costs collected under that arrangement, and the word FINE
    # in their wording describes how they are being collected rather than what
    # they are. The clerk agrees: on two Polk cases the itemization ran $79.00
    # over the summary's FINE and $79.00 under its COSTS, and $79.00 is exactly
    # these four fees, twice, to the cent. Only the fine itself, FINES AND
    # FORFEITED BAIL-STRUCTURED FINES, matched the summary's FINE on the nose.
    ('INDIGENT DEFENSE-STRUC FINES', 'COSTS'),
    ('COURT REPORTER SERVICES STRUC FINE', 'COSTS'),
    ('TIME PAYMENT FEES-STRUCTURED FINES', 'COSTS'),
    ('DOCKET PROC - STRUCT FINE', 'COSTS'),
)


def get_summary_bucket(detail):
    """Which of the five ICOS summary buckets a line item rolls up into.

    The summary is a rollup, not a fee breakdown, so reconciling a payment back
    to a fee means knowing which bucket each line fed. Getting this wrong is
    caught by the partition check in reconcile_financials rather than producing
    a wrong number, so an unrecognised fee costs us the reconciliation and
    nothing else.
    """
    text = (detail or '').upper()
    for marker, bucket in SUMMARY_BUCKET_OVERRIDES:
        if marker in text:
            return bucket
    # SURCH, not SURCHARGE, for the same reason get_finance_column reads it that
    # way: ICOS abbreviates the word when the wording runs long. Missing it here
    # is not a cosmetic miss. A surcharge read into OTHER leaves the SURCHARGE
    # bucket short by exactly that amount and OTHER over by exactly that amount,
    # which is the shortfall-and-matching-excess this partition check is built to
    # catch, so both buckets fail and the row tells the staffer two categories
    # could not be broken down when the only thing wrong was two missing letters.
    if 'SURCH' in text:
        return 'SURCHARGE'
    if 'RESTITUTION' in text:
        return 'RESTITUTION'
    if any(m in text for m in FINE_MARKERS):
        return 'FINE'
    if any(m in text for m in COSTS_MARKERS):
        return 'COSTS'
    return 'OTHER'


def _unique_paid_subset(amounts, target):
    """Indices of the rows that sum to target, when exactly one set does.

    ICOS records payments per bucket, not per line. Where only one combination
    of lines can account for the amount paid, that combination is the answer.
    Where several could, we cannot tell which fee was paid and say so.
    """
    if target == 0:
        return frozenset()
    if target == sum(amounts):
        return frozenset(range(len(amounts)))
    if len(amounts) == 1:
        return frozenset([0]) if amounts[0] == target else None
    if len(amounts) > 18:
        return None  # combinatorially unreasonable; treat as ambiguous

    from itertools import combinations
    found = None
    for size in range(1, len(amounts)):
        for combo in combinations(range(len(amounts)), size):
            if sum(amounts[i] for i in combo) != target:
                continue
            if found is not None:
                return None  # more than one explanation
            found = frozenset(combo)
    return found


def spread_over_fee_columns(due, entries, paid=None):
    """Split a category balance across the columns its own fees belong to.

    A category whose balance cannot be pinned to particular fees still has a
    composition the record does show: what each fee was assessed at. Sending the
    whole balance to one column threw that away, and the column it usually
    landed in was MISCELLANEOUS, because that is where a category label with no
    fee name in it falls.

    That is the one thing these sheets cannot survive. Bankruptcy treats columns
    J and K as the debt that is surely dischargeable and everything else as
    maybe; the 910.7 sheet reads J through P and no further; the statute of
    limitations sheet is built on old attorney fee and jail debt by name. An
    indigent defense fee that arrives as MISCELLANEOUS is a fee the hearing
    cannot ask the court to remit, and nothing on the page says why.

    So the balance is apportioned by assessment instead. It is an estimate and
    the row says so. Returns None when there is nothing to apportion over, which
    leaves the caller to fall back the old way. Every cent still comes out: the
    largest column carries the rounding.

    Apportioning is the last resort, not the first. A fee the record already
    shows as settled must not draw a share of a balance it is no longer part of,
    because the column that would collect that share is often K, and reporting
    collection costs a client does not owe is the specific defect this module
    was rewritten to stop. So anything the record can pin down is pinned down
    first, and only what is genuinely unattributable gets spread.
    """
    if not entries:
        return None
    # What each fee still shows as owed on its own line, where ICOS bothered to
    # fill the Paid column in. Most itemizations leave Paid blank, in which case
    # this is the assessment and nothing is lost.
    owed = [[get_finance_column(detail), max(amount - line_paid, Decimal(0))]
            for detail, amount, line_paid in entries]

    # A bucket paid down by more than its own lines admit to is the usual case,
    # because ICOS records the payment against the category and leaves the lines
    # alone. Where exactly one combination of those lines accounts for the
    # difference, that combination is not a guess: no other set of fees can add
    # up to what was paid. Those fees are settled and drop out of the split.
    if paid is not None:
        remainder = paid - sum((entry[2] for entry in entries), Decimal(0))
        if remainder > 0:
            settled = _unique_paid_subset([line[1] for line in owed], remainder)
            for index in settled or ():
                owed[index][1] = Decimal(0)

    weights = {}
    for column, amount in owed:
        if amount > 0:
            weights[column] = weights.get(column, Decimal(0)) + amount
    if not weights:
        # Every line reads as paid off and yet the category still owes. The
        # lines are the only account of what this money is, so fall back to
        # what each fee was assessed and let the note carry the doubt.
        for detail, amount, _line_paid in entries:
            if amount > 0:
                column = get_finance_column(detail)
                weights[column] = weights.get(column, Decimal(0)) + amount
    if not weights:
        return None
    if len(weights) == 1:
        return {next(iter(weights)): due}
    total = sum(weights.values(), Decimal(0))
    if total <= 0:
        return None

    # Largest first, so the column carrying the rounding is the one where a
    # cent is least likely to be noticed.
    order = sorted(weights, key=lambda column: (-weights[column], column))
    shares = {}
    running = Decimal(0)
    for column in order[1:]:
        share = (due * weights[column] / total).quantize(Decimal('0.01'),
                                                         rounding=ROUND_HALF_UP)
        shares[column] = share
        running += share
    shares[order[0]] = due - running
    return shares


def _bucket_partition(rows, keep_third_party, skip=frozenset()):
    """Sort the itemization into the five buckets the summary reports.

    Returns {bucket: [[detail, assessed, paid], ...]}, carrying the last
    detail across continuation rows the way itemized_financials does. A
    continuation row has no detail and no amount, only a payment against the
    line above it, so its payment is credited to that line rather than
    dropped. A skipped or excluded row breaks that chain instead, so a payment
    that follows one is dropped rather than credited to a line ICOS does not
    count.

    skip holds indexes into rows, from uncounted_collection_rows.
    """
    buckets = {name: [] for name in ICOS_BUCKETS}
    last_detail = None
    current = None
    for index, row in enumerate(rows):
        detail = row.get('detail') or ''
        if index in skip or (is_excluded_fee(detail) and not keep_third_party):
            last_detail = None
            current = None
            continue
        if not detail.strip():
            if last_detail is None:
                continue
            detail = last_detail
        else:
            last_detail = detail
        amount = row.get('amount')
        paid = row.get('paid')
        if amount is None:
            # Continuation row: a further payment against the line above.
            if current is not None and paid is not None:
                current[2] += Decimal(str(paid))
            continue
        current = [detail, Decimal(str(amount)),
                   Decimal(str(paid)) if paid is not None else Decimal(0)]
        buckets[get_summary_bucket(detail)].append(current)
    return buckets


def reconcile_financials(case):
    """Per-column amounts owed, keeping the fee breakdown and the ICOS balance.

    The summary reconciles against what ICOS says is due but collapses every fee
    into five buckets. The itemization keeps the fee detail but shows original
    assessments with the Paid column blank, so it overstates anything already
    paid. This partitions the itemization into the buckets the summary reports,
    checks each bucket against its summary original, and then subtracts the
    bucket's payment from the specific lines that account for it.

    Reconciling is per category rather than per case: a category that does not
    add up is reported as a summary total, and the categories that do add up
    still get their fee breakdown.

    Returns (columns, note). columns is None when nothing on the row could be
    reconciled, which means the caller should fall back to the summary. note is
    None when the whole row is fee by fee, and otherwise names the categories
    that are not, either because they did not add up or because their payment
    could not be attributed to particular lines.
    """
    categories = case.get('summary_categories')
    rows = case.get('financials')
    if not categories or not rows:
        return None, None

    summary = {}
    for category in categories:
        if category.get('original') is None:
            return None, None
        summary[category['label'].upper()] = category
    if set(summary) != set(ICOS_BUCKETS):
        return None, None

    # A third party collection fee is normally ICOS listing a debt it does not
    # count, so it is dropped before the buckets are checked. When this case's
    # summary does count it, dropping it would break the bucket it belongs to.
    keep_third_party = summary_counts_third_party(case)
    buckets = _bucket_partition(rows, keep_third_party,
                                uncounted_collection_rows(case))

    # A bucket has to add up to what the summary says was assessed before any
    # payment is attributed off it. A fee read into the wrong bucket shows up as
    # a shortfall in one and a matching excess in another, so both fail and
    # neither is quietly wrong, which is why a bucket that does match to the cent
    # is good evidence its partition is right.
    #
    # A bucket that does not match is reported the way summary_financials would
    # report it and the rest of the row keeps its fee breakdown. Giving up on the
    # whole row instead emptied columns J, K and L, and those are where the
    # statute-of-limitations and Polk room-and-board sheets look for twenty year
    # old attorney fee and jail debt. Old cases are both the ones that sheet is
    # for and the ones most likely to have an itemization that will not
    # reconcile, so the two lined up badly.
    columns = {}
    unresolved = []
    unreconciled = []
    apportioned = []
    carrying = []
    # Which columns each category's balance actually reached. The note below
    # describes where the money went, and the only way to describe that
    # correctly is to have watched it land.
    landed = {}
    for name in ICOS_BUCKETS:
        entries = buckets[name]
        category = summary[name]
        if entries or category['original']:
            carrying.append(name)

        assessed = sum((entry[1] for entry in entries), Decimal(0))
        if abs(assessed - category['original']) > Decimal('0.01'):
            unreconciled.append(name)
            due = category.get('due')
            if due is None:
                due = category['original'] - (category.get('paid') or Decimal(0))
            if due:
                # The total is the summary's, because the itemization did not
                # agree with it and the summary is the side ICOS stands behind.
                # Which columns it belongs in is still the itemization's to say.
                shares = spread_over_fee_columns(due, entries,
                                                 category.get('paid'))
                if shares is None:
                    shares = {get_finance_column(category['label']): due}
                elif len(shares) > 1:
                    # One column is not an estimate. The category total went
                    # somewhere exact and the note has nothing to add.
                    apportioned.append(name)
                for column, share in shares.items():
                    columns[column] = columns.get(column, Decimal(0)) + share
                landed[name] = {column for column, share in shares.items()
                                if share}
            continue

        if not entries:
            continue
        paid = category.get('paid') or Decimal(0)

        # The itemization's own Paid column is blank on most fees, but not on
        # all of them: restitution in particular is paid down in instalments
        # that ICOS lists line by line. Where those line payments account for
        # everything the summary says was paid in this bucket, there is nothing
        # to infer and no ambiguity to flag.
        by_line = sum((entry[2] for entry in entries), Decimal(0))
        if abs(by_line - paid) <= Decimal('0.01'):
            for detail, amount, line_paid in entries:
                owed = amount - line_paid
                if owed <= 0:
                    continue
                column = get_finance_column(detail)
                columns[column] = columns.get(column, Decimal(0)) + owed
            continue

        amounts = [entry[1] for entry in entries]
        settled = _unique_paid_subset(amounts, paid)
        if settled is None:
            # Cannot say which line was paid. The balance still belongs to this
            # bucket, though, so it goes to the column the bucket's lines share
            # and only falls back to MISC when they disagree. Sending a
            # restitution balance to MISC because a partial payment could not be
            # split across instalments was losing the one distinction the
            # spreadsheet most needs to keep.
            unresolved.append(name)
            due = summary[name].get('due')
            if due is None:
                due = sum(amounts, Decimal(0)) - paid
            shares = spread_over_fee_columns(due, entries, paid)
            if shares is None:
                shares = {get_finance_column(summary[name]['label']): due}
            elif len(shares) > 1:
                apportioned.append(name)
            for column, share in shares.items():
                columns[column] = columns.get(column, Decimal(0)) + share
            continue
        for index, (detail, amount, _line_paid) in enumerate(entries):
            owed = Decimal(0) if index in settled else amount
            if owed == 0:
                continue
            column = get_finance_column(detail)
            columns[column] = columns.get(column, Decimal(0)) + owed

    if carrying and not set(carrying) - set(unreconciled):
        # Nothing on this row reconciled, so what came out is the summary with
        # extra steps. Hand it back to the caller, which says so in one sentence
        # rather than five.
        return None, None

    notes = []
    if unreconciled:
        text = ("%s did not add up against the itemization, so %s "
                "ICOS's category total rather than a per-fee breakdown"
                % (_and_list(unreconciled),
                   "those balances are" if len(unreconciled) > 1
                   else "that balance is"))
        # Only the categories whose balance really did end up in MISCELLANEOUS.
        # Deciding this from the category's label instead said MISCELLANEOUS
        # about any unreconciled COSTS, including the ones whose fees all
        # pointed at one real column and were broken out there exactly. On the
        # captured corpus that put a four figure jail debt in JAIL / ROOM &
        # BOARD and then told the attorney it was a lump sum, which is the
        # reading that loses a room-and-board appeal. A category that placed no
        # money anywhere is not named at all: there is no cell to go look at.
        stranded = [name for name in unreconciled
                    if name not in apportioned and landed.get(name) == {'O'}]
        if len(stranded) > 1:
            text += (", and %s are in MISCELLANEOUS rather than in their own "
                     "columns" % _and_list(stranded))
        elif stranded:
            text += (", and %s is in MISCELLANEOUS rather than in its own "
                     "columns" % stranded[0])
        notes.append(text + ". The rest of the row is fee by fee and the ICOS "
                            "total is still right.")
    if unresolved:
        notes.append(
            "ICOS records payments per category, not per fee. The payment "
            "in %s could not be tied to a specific line, so that balance is "
            "ICOS's category total." % _and_list(unresolved))
    if apportioned:
        # Named separately from the reason, because this is the part that
        # changes how the number should be read. The column is right and the
        # split inside it is an estimate.
        notes.append(
            "The %s balance is divided across its fee columns in proportion "
            "to what those fees were assessed, so the column totals are "
            "estimates. Request an accounting before relying on the split."
            % _and_list(sorted(set(apportioned))))
    return columns, " ".join(notes) or None


def _and_list(names):
    if len(names) == 1:
        return names[0]
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def is_excluded_fee(detail):
    """Fees ICOS lists but does not count toward the balance.

    A third-party (Linebarger) collection fee appears as a line item, yet ICOS
    leaves it out of the case totals entirely -- summing the itemization at face
    value put money in the collection-costs column that the defendant is not
    shown as owing.

    Usually. summary_counts_third_party is the case-by-case check, because on
    four of the nine captured cases carrying one of these fees ICOS did count it.
    """
    return "THIRD PARTY" in (detail or "").upper()


def summary_counts_third_party(case):
    """Whether this case's ICOS summary already includes its third party fees.

    Of the nine captured cases carrying a third party collection fee, five leave
    it out of the five bucket summary, which is what is_excluded_fee describes.
    On the other four the itemization and the summary agree to the cent, and
    that is ICOS saying it did count the fee.

    Excluding the line on one of those four costs the fee breakdown rather than
    the money. The bucket then falls short of its summary original, so it stops
    reconciling and the balance comes back as a category total, spread over
    whatever columns the rest of the bucket uses. That takes collection costs
    out of column K, which is one of the two columns Iowa Legal Aid treats as
    surely dischargeable in a bankruptcy.

    All four of those captured cases are paid off, so nothing on the corpus
    moves either way. This is here so that the first one that is not paid off
    keeps its collection costs in column K.
    """
    categories = case.get('summary_categories') or []
    rows = case.get('financials') or []
    if not categories or not rows:
        return False
    if not any(is_excluded_fee(row.get('detail')) for row in rows):
        return False

    assessed = Decimal(0)
    for row in rows:
        # Rows with no amount are continuation payments against the line above,
        # which the summary counts under that line rather than separately.
        if row.get('amount') is not None:
            assessed += Decimal(str(row['amount']))
    summarised = Decimal(0)
    for category in categories:
        if category.get('original') is None:
            return False
        summarised += category['original']
    return abs(assessed - summarised) <= Decimal('0.01')


def uncounted_collection_rows(case):
    """Indexes of itemization rows this case's ICOS summary leaves out.

    The county attorney's collection fee is usually part of the case balance,
    filed under FINE, which is what the SUMMARY_BUCKET_OVERRIDES entry says
    and what most captured cases carrying one show. On two of the 400 the
    clerk's own arithmetic says the opposite. A Black Hawk felony case
    counts its state and county collection splits in FINE and leaves the bare
    COLLECTION BY CO ATTY fee out entirely, so the itemization runs over the
    summary by exactly that fee. A Union County case goes further: its raw
    ICOS page lists every collection fee, plus a second CRIME SERVICES
    SURCHARGE ledger entry identical to the paid one but with no payment, no
    receipt and no date, and its summary counts none of them. Those are
    superseded entries from the collection process, not debt, and the clerk's
    total says so: original amounts equal to the summary only once the
    duplicates and the collection rows are set aside.

    A simple misdemeanor from a third county shows the same duplicate with its
    wording recoded: an unpaid surcharge under a legacy DNU (do not use) fee
    code, same amount as the paid surcharge next to it under the current code,
    and the summary counts only the paid one. Other cases from the same county
    carry unpaid DNU rows their summaries do count, so a legacy code alone
    proves nothing; the candidate is the pairing, an unpaid DNU row whose
    amount matches a row the clerk marked paid in the same bucket.

    Which fees a summary leaves out varies case by case, the way third party
    fees already do, so this is decided per case and only on the clerk's own
    arithmetic. Candidates are grouped, collection fees by their exact wording,
    unpaid rows that duplicate an identical earlier row together, and unpaid
    legacy-coded rows shadowing a paid amount in their bucket together, and a
    union of groups is excluded only when the itemization exceeds the summary
    by exactly that union's sum, every one of the five buckets then matches
    its summary original to the cent, and no other union manages the same.
    A wrong exclusion would need all three to conspire. When no union
    qualifies, nothing is excluded and the partition check fails the way it
    does today, which is the safety net all of these guesses share.
    """
    categories = case.get('summary_categories') or []
    rows = case.get('financials') or []
    if not categories or not rows:
        return frozenset()
    summary = {}
    for category in categories:
        if category.get('original') is None:
            return frozenset()
        summary[category['label'].upper()] = category
    if set(summary) != set(ICOS_BUCKETS):
        return frozenset()

    # The paid counterpart of a recoded duplicate can sit on either side of
    # it in the ledger, so paid amounts are collected before grouping.
    paid_amounts = set()
    for row in rows:
        detail = (row.get('detail') or '').strip().upper()
        if row.get('amount') is None or row.get('paid') is None:
            continue
        paid_amounts.add((get_summary_bucket(detail), str(row['amount'])))

    groups = {}
    seen = set()
    for index, row in enumerate(rows):
        detail = (row.get('detail') or '').strip().upper()
        amount = row.get('amount')
        if amount is None:
            continue
        if 'COLLECTION BY CO ATTY' in detail:
            groups.setdefault(('fee', detail), []).append(index)
        elif (detail, str(amount)) in seen and row.get('paid') is None:
            groups.setdefault(('duplicate', detail, str(amount)),
                              []).append(index)
        elif (detail.startswith('DNU') and row.get('paid') is None
              and (get_summary_bucket(detail), str(amount)) in paid_amounts):
            groups.setdefault(('dnu', detail, str(amount)),
                              []).append(index)
        seen.add((detail, str(amount)))
    # The union search below is exhaustive, so it needs a bound; ten groups is
    # far past anything captured (Union County, the worst, has five).
    if not groups or len(groups) > 10:
        return frozenset()

    keep_third_party = summary_counts_third_party(case)
    assessed = Decimal(0)
    for row in rows:
        if row.get('amount') is None:
            continue
        if is_excluded_fee(row.get('detail')) and not keep_third_party:
            continue
        assessed += Decimal(str(row['amount']))
    summarised = sum(summary[name]['original'] for name in ICOS_BUCKETS)
    overage = assessed - summarised
    if overage <= Decimal('0.01'):
        return frozenset()

    sums = {key: sum(Decimal(str(rows[index]['amount']))
                     for index in indexes)
            for key, indexes in groups.items()}
    keys = sorted(groups)
    winners = []
    for size in range(1, len(keys) + 1):
        for combo in itertools.combinations(keys, size):
            if sum(sums[key] for key in combo) != overage:
                continue
            skip = frozenset(index for key in combo for index in groups[key])
            buckets = _bucket_partition(rows, keep_third_party, skip)
            if all(abs(sum((entry[1] for entry in buckets[name]), Decimal(0))
                       - summary[name]['original']) <= Decimal('0.01')
                   for name in ICOS_BUCKETS):
                winners.append(skip)
    if len(winners) == 1:
        return winners[0]
    return frozenset()


def summary_financials(case):
    """Per-column amounts owed, taken from the ICOS summary table.

    The summary reflects payments; the itemization does not. Where ICOS breaks
    the balance out by category, that is the number to report.
    """
    columns = {}
    for category in case.get('summary_categories') or []:
        if is_excluded_fee(category['label']):
            continue
        due = category['due']
        if due is None:
            continue
        column = get_finance_column(category['label'])
        columns[column] = columns.get(column, Decimal(0)) + due
    return columns


def itemized_financials(case):
    financials = {}
    col = None
    previous_col = None

    for f in case['financials']:
        detail = f['detail'] or ''
        if is_excluded_fee(detail):
            # Not part of what ICOS says is owed; counting it inflated the
            # collection-costs column.
            previous_col = None
            continue

        if not detail.strip():
            if previous_col is not None:
                col = previous_col
            else:
                continue  # Skip only if we have no previous category
        else:
            # For rows with non-blank details, get new column categorization
            col = get_finance_column(detail)
            previous_col = col

        if col not in financials:
            financials[col] = Decimal(0)

        amount = f['amount'] if f['amount'] is not None else '0'
        paid = f['paid'] if f['paid'] is not None else '0'
        financials[col] += Decimal(amount)
        financials[col] -= Decimal(paid)

    return financials


# What the fee columns on a row are worth, said plainly, for the rows where
# they are not a straight per-fee breakdown. A reconciled row says nothing,
# because a note on every row is a note nobody reads.
SUMMARY_ONLY_NOTE = (
    "Fee columns are ICOS's five summary categories, not a per-fee breakdown. "
    "The itemization could not be reconciled against them, so sheriff, "
    "indigent defense, jail and probation fees are inside MISCELLANEOUS "
    "rather than in their own columns. The ICOS total is still right."
)

ITEMIZED_ONLY_NOTE = (
    "ICOS gave no category summary for this case, so the fee columns come "
    "from the itemization. ICOS leaves the itemization's paid column blank on "
    "most fees, so anything already paid may still be counted here. Treat "
    "these as assessed rather than owed."
)


def process_financials(case, worksheet, row):
    # Reconciling the itemization against the summary is the only path that
    # keeps the fee breakdown and lands on the balance ICOS reports, so it is
    # tried first. The summary alone is right about the total and collapses
    # every fee into five buckets. The itemization alone keeps the fees and
    # overstates whatever has been paid. Both remain as fallbacks, and a row
    # that used one says so, because a sheriff fee that is missing because it
    # was folded into MISCELLANEOUS looks exactly like one that was never
    # charged.
    financials, note = reconcile_financials(case)
    source = 'reconciled'
    if financials is None:
        financials = summary_financials(case)
        source = 'summary'
        note = SUMMARY_ONLY_NOTE
        if not financials:
            financials = itemized_financials(case)
            source = 'itemized'
            note = ITEMIZED_ONLY_NOTE

    total_due = None
    if 'total_due' in case:
        total_due = Decimal(case['total_due'].replace('$', '').replace(',', ''))

    for column, value in financials.items():
        worksheet[column + str(row)] = value
    if total_due is not None:
        worksheet['U' + str(row)] = total_due

    # A caveat about where the fee columns came from is only worth reading if
    # there are fees in them. ICOS reporting nothing due means every fee column
    # on the row is zero however Napier arrived at it, so the caveat describes a
    # breakdown of no money. That is not a rare shape: of the 25 captured pages
    # that carried one of these, 23 owed nothing. Column V is also where the
    # notes that matter go, a disposition Napier had to guess at and a case Iowa
    # Courts would not give up, and a column staff have learned to skip past is
    # worse than an empty one.
    #
    # Only this caveat goes quiet. The mismatch note below still fires on a paid
    # off case, because fee columns adding up to something against a zero
    # balance is a real disagreement and flagging it is that note's whole job.
    nothing_owed = total_due is not None and total_due == 0
    notes = [note] if note and not nothing_owed else []

    # If the per-category figures still don't add up to the balance ICOS
    # reports, the ICOS figure (column U) is the one to trust -- flag the row so
    # staff don't take the categories at face value.
    flagged = False
    if total_due is not None:
        categorized = sum(financials.values(), Decimal(0))
        if abs(categorized - total_due) > Decimal('0.01'):
            flagged = True
            cell_u = worksheet['U' + str(row)]
            cell_u.fill = MISMATCH_FILL
            cell_u.font = MISMATCH_FONT
            notes.append(
                "Category fees total $%s but ICOS shows $%s due (%s figures) - trust "
                "the ICOS total; the difference is usually payments or third-party "
                "collection fees ICOS no longer counts"
                % (categorized, total_due, source)
            )

    if notes:
        cell_v = worksheet['V' + str(row)]
        cell_v.value = " ".join(notes)
        if flagged:
            cell_v.font = MISMATCH_FONT

def append_note(worksheet, row, text):
    """Add to column V without displacing what process_financials put there.

    A row can be both reconciled from the summary and coded on a guess, and the
    two notes are about different things. Whichever is written second joins the
    first rather than replacing it.
    """
    cell = worksheet['V' + str(row)]
    cell.value = ("%s %s" % (cell.value, text)).strip() if cell.value else text


def owes_money(worksheet, row):
    """Whether this row's fee columns add up to anything, read back off the sheet.

    The same SUM(J:S) the analysis sheets take, asked after process_financials
    has written them, so a caveat about what one of those sheets will print only
    goes on a row that sheet is going to print about.
    """
    total = Decimal(0)
    for column in 'JKLMNOPQRS':
        value = worksheet[column + str(row)].value
        if value in (None, ''):
            continue
        try:
            total += Decimal(str(value))
        except InvalidOperation:
            continue
    return total > 0


def process_case(case, worksheet, row, as_of=None):
    """Write one case into CASE DATA. Returns the dispositions it could not read.

    Almost always empty. When it is not, the case is on the sheet under a code
    Napier guessed at, and the return value is how the run gets to say so.

    Each wording comes back paired with whether the row ended up carrying a
    code, because the two cases the run has to describe are not the same one. A
    count whose adjudication could not be read is coded OTH; a case with nothing
    adjudicated at all leaves column G empty and three sheets call it an open
    charge. The pairing is what stops the run reporting the second as the first,
    and it is read off column G for the same reason the note is.

    as_of is the clinic date, the same one build_workbook puts in BASIC INFO B3.
    Nothing here reads it since column I went back to the staff. It is kept
    because every caller passes it and because a date-sensitive cell landing in
    this row again should be answered against the day of the clinic rather than
    the day the file is reopened.
    """
    if as_of is None:
        as_of = iowa_today()
    i = str(row)
    worksheet['A' + i] = case['id']
    worksheet['B' + i] = case['county']
    charge = get_dominant_charge(case['charges'], case['id'])
    ordinance_note = None

    cell_E = worksheet['E' + i] # Get cell E

    if charge is None:
          worksheet['C' + i] = case['summary_created_date']
          worksheet['D' + i] = case['summary_disposition_date']
          # come back later and do this with a map / dictionary
          description_text = ""
          if case['id'][7:9]=="DR":
              description_text = "Domestic relations [civil] - " + case['summary_dispo_status']
          elif case['id'][7:9]=="DA":
              description_text = "Domestic abuse [civil] - " + case['summary_dispo_status']
          elif case['id'][7:9]=="SC":
              description_text = "Small claims - " + case['summary_dispo_status']
          elif case['id'][7:9]=="PC":
              description_text = "post conviction relief - " + case['summary_dispo_status']
          else:
              description_text = "other civil - " + case['summary_dispo_status']
          cell_E.value = description_text
          worksheet['F' + i] = "n/a"
          worksheet['G' + i] = "CIV"
          process_financials(case, worksheet, i)
          # return # This was here, but we want to apply alignment, so moved it down
    else:
        description = charge['description']
        disposition = charge['disposition']
        disposition_date = charge['dispositionDate']

        if not disposition:
            # No count on this case has been adjudicated. ICOS still prints a
            # status and a date for the case as a whole, and Napier parsed both
            # off the summary page and then used them only on the civil path.
            #
            # The date is the one that hurts. A blank column D is how this
            # workbook detects an open case: the EXPUNGEMENT sheet's POSSIBLE
            # PENDING CHARGES column counts case rows that have no disposition
            # date. So a case ICOS closed in 1993 and one it dismissed in 2021
            # were both being counted as live charges hanging over the client,
            # and a pending charge is what blocks expungement under 901C.2.
            # Filling it from the summary is also what keeps that column honest
            # in the other direction: the genuinely pending case has no date on
            # the summary either, so it stays blank and still counts.
            description = description or charge.get('original_description') or ''
            disposition_date = disposition_date or case.get(
                'summary_disposition_date') or ''
            status = (case.get('summary_dispo_status') or '').strip()
            if status:
                code = case_level_code(status)
                if code is None:
                    charge.setdefault('unknown_dispositions', [])
                    if status not in charge['unknown_dispositions']:
                        charge['unknown_dispositions'] = sorted(
                            charge['unknown_dispositions'] + [status])
                else:
                    disposition = code
            if not disposition and is_old_case_code(charge):
                # OLD CASE CHARGE CODE is what Iowa's clerks put on a case that
                # predates the electronic docket. There is no adjudication to
                # read because the disposition is on paper in a courthouse
                # basement, and the status is CLOSED, which is not a
                # disposition Napier can translate.
                #
                # Left empty, column G is 0 to Excel, and the SOL, BANKRUPTCY
                # and EXEMPTIONS sheets each render IF(G=0, "open charge", G).
                # So a case closed in 1993 was printing as an open charge on
                # three sheets. OTH is a string, so those sheets print OTH, and
                # OTH appears in no formula anywhere in either template, so it
                # is in no cleared set and the money does not move. The one
                # captured case owes $197.43 and stays in the same buckets on
                # all three sheets.
                #
                # Keyed on the charge code and not on the status. CLOSED turns
                # up on cases that have a real adjudication to read, and this
                # must not speak for any of those.
                disposition = "OTH"

        # After the clerk's wording has had its say and before anything is
        # written, because what the charge is beats how the disposition was
        # typed. This is the only place the statute overrides the code.
        if only_civil_sections(charge['charge']):
            disposition = "CIV"

        worksheet['C' + i] = charge['offenseDate']
        worksheet['D' + i] = disposition_date
        cell_E.value = description
        worksheet['F' + i] = charge['charge']
        worksheet['G' + i] = disposition
        # Only when we can tell. is_vehicular returns None for a case with no
        # adjudicated statute, and the cell is left alone rather than told "NO".
        vehicular = is_vehicular(charge['charge'])
        if vehicular is not None:
            worksheet['H' + i] = vehicular
        elif charge['charge'] and disposition in LICENCE_DISPOSITIONS:
            # Held until the fees are in, because LICENSE-REGIS only prints its
            # verdict on a case that owes something and this note is only worth
            # reading where that verdict appears.
            ordinance_note = ORDINANCE_VEHICULAR_NOTE % charge['charge']

        # The EXPUNGEMENT sheet's public intoxication, PAULA and prostitution
        # columns read these two rather than picking column F apart themselves.
        # Column F is a semicolon joined list of every count's statute and the
        # statutes carry subsections and ordinance prefixes, so answering this
        # in Excel meant string surgery on a list, which is how those columns
        # came to be an equality test that never matched anything. Blank where
        # Napier cannot tell, which those columns read as NO exactly as column H
        # is read today.
        #
        # AJ and AK rather than AI, which is where both templates dragged a
        # thirteenth splitter slot on row 9 that statutes._clear_strays exists
        # to blank. A case landing on row 9 would have put a value there and
        # left the stray in place.
        for column, sections in (('AJ', PUBLIC_INTOX_PAULA_SECTIONS),
                                 ('AK', PROSTITUTION_SECTIONS)):
            answer = cites_section(charge['charge'], sections)
            if answer is not None:
                worksheet[column + i] = answer

    cell_E.alignment = Alignment(wrap_text=True) # Apply text wrapping

    # If charge was None, we still need to process financials if it wasn't returned early
    if charge is None:
        # process_financials(case, worksheet, i) # Already called above if charge is None
        return [] # Now we can return

    process_financials(case, worksheet, i)

    # After process_financials, which owns column V, so a note joins whatever it
    # wrote about the money rather than being overwritten by it.
    if ordinance_note and owes_money(worksheet, i):
        append_note(worksheet, i, ordinance_note)
    spread = charge.get('disposition_date_spread') or []
    if spread and owes_money(worksheet, i):
        append_note(worksheet, i, DISPOSITION_SPREAD_NOTE
                    % (", ".join(spread), disposition_date))
    # Only where the SOL sheet has something to be wrong about. A pending case
    # with nothing outstanding puts zero in all three of its columns whatever
    # column D says, and a caveat there describes a decision nobody is making.
    if not disposition_date and owes_money(worksheet, i):
        append_note(worksheet, i, PENDING_CASE_NOTE)
    # JUV survived on a case that is not the juvenile court's, which means an
    # adjudication was the only disposition on the page. Unlike the spread and
    # ordinance notes this one is not conditional on the row owing anything,
    # because the code itself is what may be wrong and it is read by the licence
    # and expungement sheets whether or not there is a dollar on the row.
    if charge.get('disposition') == 'JUV' and not is_juvenile_case(case['id']):
        append_note(worksheet, i, ADULT_ADJUDICATION_NOTE)
    unknown = charge.get('unknown_dispositions') or []
    if not unknown:
        return []
    # Which of the two notes belongs here is decided by the cell the sheets
    # actually read, not by which branch above collected the wording. A count
    # Napier could not read still leaves a code in column G, and the OTH note
    # describes what the sheets do with it. A case with nothing adjudicated
    # leaves column G empty, and the sheets do something else entirely with
    # that.
    coded = bool(worksheet['G' + i].value)
    append_note(worksheet, i,
                (UNKNOWN_DISPOSITION_NOTE if coded
                 else UNCODED_CASE_STATUS_NOTE) % ", ".join(unknown))
    # The same answer goes back to the caller rather than being worked out a
    # second time. The run's account of the row and the row's account of itself
    # were allowed to disagree once already.
    return [(wording, coded) for wording in unknown]
