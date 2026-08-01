"""The work that runs inside background jobs.

Both tasks own an ICOS session end to end so it is always released: the search
task hands its live session to the store (the reaper closes it if the user
walks away), and the CRS task logs off when it finishes or fails.
"""

import datetime
import os
import platform

from openpyxl import load_workbook
from werkzeug.utils import secure_filename

import actions
import alerts
import case_parser
import crs
import icos_sessions
from icos import IcosClient, IcosError

# One case ICOS will not hand over is a bad case. Several in a row is the site
# being down, and there is no point walking the rest of the list to find that
# out one four-minute budget at a time.
CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE = 3

tmp_dir = '/tmp/'
if platform.system() == 'Windows':
    tmp_dir = '.\\tmp\\'


def _defendant_key(case):
    if case['dob'] and not case['dob'].isspace():
        month, day, year = [part.strip() for part in case['dob'].split('/')]
        return "{}-{}-{} {}".format(year, month, day, case['name'].strip())
    return 'DOB-UNKNOWN ' + case['name']


def group_cases(cases):
    """Group search hits by defendant, the way the results page lists them."""
    case_dict = {}
    for case in cases:
        case_dict.setdefault(_defendant_key(case), []).append(case['id'])
    return case_dict, sorted(case_dict)


def report_novel_roles(job, cases):
    """Say when a search included a role nobody has classified yet.

    Napier decides who is a party by listing the roles that are not one, so a
    role it has never seen is included by default. That has gone wrong four
    times, and every time the tell was a person reading the workbook and asking
    why a stranger's convictions were in it. There is nothing to see server side
    because nothing fails: the case is fetched, parsed and written normally.

    This does not change what goes in the workbook. It reports the role and
    nothing else, so the same PII rule the rest of alerting keeps holds here:
    the client's name and date of birth are the privileged part and never leave
    the machine, and a role string on its own identifies nobody.
    """
    novel = sorted({case['role'] for case in cases
                    if case['role'] not in case_parser.KNOWN_PARTY_ROLES})
    for role in novel:
        alerts.record(job.id[:8], job.kind, alerts.NOVEL_ROLE, role=role)
    return novel


def report_unknown_dispositions(job, unknown):
    """Say when a case was coded on a guess, on the page and by email.

    charge_code_map has a word for every outcome anyone has seen ICOS use.
    Anything else codes the case OTH, and OTH is what the expungement,
    bankruptcy, exemption and licence sheets read as no conviction, so a client
    with a real conviction can come out of four sheets looking clean. The row
    itself says so in column V, but the workbook goes to whoever runs the
    clinic and the map only gets the missing word added if it reaches Alex.

    The wording and the case number go out. Both are court public record and
    neither is any use without the other: the word is what the map is missing
    and the case is the page to read it off. The client is not in it.
    """
    for disposition, case_ids in sorted(unknown.items()):
        job.log("Iowa Courts recorded \"%s\" on %d case%s, which Napier does not "
                "recognise. Those rows are coded OTH and say so in the notes "
                "column: %s."
                % (disposition, len(case_ids), "" if len(case_ids) == 1 else "s",
                   ", ".join(case_ids)))
        alerts.record(job.id[:8], job.kind, alerts.UNKNOWN_DISPOSITION,
                      progress=alerts.recent_progress(job),
                      disposition=disposition,
                      cases=", ".join(case_ids))
    return sorted(unknown)


def search_task(job, username, password, firstname, middlename, lastname):
    client = IcosClient(log=job.log, alert=alerts.emitter(job))
    keep_session = False
    try:
        client.login(username, password)
        body = client.search(firstname, middlename, lastname)

        job.log("Reading results...")
        cases, too_many_results = case_parser.parse_search(body)
        report_novel_roles(job, cases)
        case_dict, keys = group_cases(cases)

        token = icos_sessions.put(client)
        keep_session = True
        job.result = {
            "cases": case_dict,
            "keys": keys,
            "too_many_results": too_many_results,
            "session_token": token,
        }
        job.log("Found %d case%s across %d name%s."
                % (len(cases), "" if len(cases) == 1 else "s",
                   len(keys), "" if len(keys) == 1 else "s"))
        return "/results/" + job.id
    finally:
        if not keep_session:
            client.logoff()
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def crs_task(job, session_token, keys, case_dict, def_name, def_dob, is_lite):
    client = icos_sessions.claim(session_token)
    if client is None:
        # Two ordinary things land here. Staff left the results page open past
        # the idle timeout and the reaper logged the session off, or a second
        # submit arrived while the first was already running and claimed it.
        # Neither is a bug, so neither should reach staff as "something went
        # wrong inside Napier" or reach an inbox as an alert.
        raise IcosError("That search is no longer signed in to Iowa Courts "
                        "Online. Please run the search again.")
    client.set_alert(alerts.emitter(job))

    try:
        case_ids = []
        for key in keys:
            case_ids.extend(case_dict.get(key, []))

        cases = []
        failed = []
        failures_in_a_row = 0
        not_attempted = []
        for index, case_id in enumerate(case_ids, start=1):
            job.log("Pulling case %d of %d (%s)..." % (index, len(case_ids), case_id),
                    count=index - 1, total=len(case_ids))
            case = {'id': case_id}
            try:
                summary, charges, financials = client.case_bundle(case_id)
            except IcosError as e:
                # ICOS never gave up this case inside its retry budget. That
                # costs one row, not the run: the other cases are still worth
                # pulling and the workbook still gets built without this one.
                print("Case %s could not be retrieved: %r" % (case_id, e), flush=True)
                alerts.record(job.id[:8], job.kind, alerts.CASE_UNAVAILABLE,
                              progress=alerts.recent_progress(job),
                              case=case_id,
                              note="%s The run carried on without it."
                                   % e.message)
                failed.append(case_id)
                failures_in_a_row += 1
                if failures_in_a_row >= CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE:
                    not_attempted = case_ids[index:]
                    break
                continue
            failures_in_a_row = 0
            try:
                case_parser.parse_case_summary(summary, case)
                case_parser.parse_case_charges(charges, case)
                case_parser.parse_case_financials(financials, case)
            except Exception as e:
                # One unparseable case should not cost staff the whole run --
                # collect it, report it, and build the CRS from the rest.
                print("Case %s failed to parse: %r" % (case_id, e), flush=True)
                # The case id is in the alert on purpose: it is court public
                # record, and without it nobody can go look at the page that
                # broke the parser.
                alerts.record(job.id[:8], job.kind, alerts.PARSE_FAILURE,
                              progress=alerts.recent_progress(job),
                              case=case_id,
                              **{'traceback': alerts.safe_traceback(e)})
                failed.append(case_id)
                continue
            cases.append(case)

        if not_attempted:
            failed.extend(not_attempted)
            job.log("Iowa Courts stopped responding, so the last %d case%s could not "
                    "be pulled. The workbook has the rest."
                    % (len(not_attempted), "" if len(not_attempted) == 1 else "s"))

        if not cases:
            raise ValueError("no cases could be read")

        job.log("Building the CRS workbook...", count=len(case_ids), total=len(case_ids))
        path, unknown_dispositions = build_workbook(cases, def_name, def_dob, is_lite)
        report_unknown_dispositions(job, unknown_dispositions)
        job.result = {
            "file": path,
            "def_name": def_name,
            "is_lite": is_lite,
            "written_cases": len(cases),
            "requested_cases": len(case_ids),
            "failed_cases": failed,
        }

        if failed:
            job.log("%d of %d cases retrieved. These could not be read and are not in "
                    "the workbook: %s." % (len(cases), len(case_ids), ", ".join(failed)))
        else:
            job.log("Done. %d case%s written."
                    % (len(cases), "" if len(cases) == 1 else "s"))
        # The finish page, not the file. Sending the browser straight at the
        # download meant the line above -- the one naming the cases that are
        # missing from the workbook -- flashed past and was gone, and a staffer
        # who lost the file to a full disk or a stray click had to burn another
        # ICOS session to get it back.
        return "/done/%s" % job.id
    finally:
        # The session was claimed, so nothing else will release it.
        client.logoff()
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def build_workbook(cases, def_name, def_dob, is_lite):
    """Returns the path written, and the dispositions Napier could not read.

    The second value is a map of the ICOS wording to the case numbers it turned
    up on. Empty on almost every run. When it is not, those cases are on the
    sheet under a guessed code and somebody needs to know before the workbook is
    used, so the caller reports it rather than the file quietly carrying it.
    """
    workbook = load_workbook('CRS Lite 3.5.5.xlsx' if is_lite else 'CRS 3.5.5.xlsx')
    sheet = workbook['CASE DATA']
    # One clinic date for the whole workbook, read once. Column I asks whether a
    # probation term is still running, which is only answerable against a day,
    # and it has to be the same day BASIC INFO B3 gets below or the workbook
    # disagrees with itself about when it was built.
    clinic_date = datetime.date.today()
    row = 4
    unknown = {}
    for case in cases:
        for disposition in crs.process_case(case, sheet, row, clinic_date) or []:
            unknown.setdefault(disposition, []).append(case['id'])
        row += 1

    sheet = workbook['BASIC INFO']
    # Every date test in the workbook compares against B3, the clinic date: the
    # twenty year cut on the SOL sheet, the two year and eight year expungement
    # waits, whether the client has turned 18. Left blank it reads as zero, so
    # nothing is ever old enough and the SOL sheet reports every case as having
    # no argument, which looks exactly like a client with no stale debt. Today
    # is the right default for a workbook built today, and it is a date value
    # rather than text so the arithmetic works. Staff can overwrite it for a
    # clinic on another day.
    sheet['B3'] = clinic_date
    sheet['B3'].number_format = 'MM/DD/YYYY'
    sheet['B5'] = def_name.strip()
    sheet['B6'] = def_dob

    # Last, and only after BASIC INFO, because the action list reads CASE DATA
    # back rather than recomputing it and puts the client's name at the top.
    actions.build_action_sheet(workbook, cases, row - 4, clinic_date,
                               def_name.strip())

    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)

    # def_name is user-supplied; sanitize it before using it in a filesystem path
    safe_name = secure_filename(def_name.strip().replace(' ', '_')) or "case"
    stamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    suffix = "_Lite_CRS_" if is_lite else "_CRS_"
    path = tmp_dir + safe_name + suffix + stamp + ".xlsx"
    workbook.save(path)
    return path, unknown


def download_name(def_name, is_lite):
    parts = [def_name.strip().replace(" ", "_")]
    if is_lite:
        parts.append("Lite")
    parts.append(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    return "%s.xlsx" % "_".join(parts)
