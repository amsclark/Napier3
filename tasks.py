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

import alerts
import case_parser
import crs
import icos_sessions
from icos import IcosClient

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


def search_task(job, username, password, firstname, middlename, lastname):
    client = IcosClient(log=job.log, alert=alerts.emitter(job))
    keep_session = False
    try:
        client.login(username, password)
        body = client.search(firstname, middlename, lastname)

        job.log("Reading results...")
        cases, too_many_results = case_parser.parse_search(body)
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
    client = icos_sessions.get(session_token)
    if client is None:
        raise LookupError("session expired")
    client.set_alert(alerts.emitter(job))

    try:
        case_ids = []
        for key in keys:
            case_ids.extend(case_dict.get(key, []))

        cases = []
        failed = []
        for index, case_id in enumerate(case_ids, start=1):
            job.log("Pulling case %d of %d (%s)..." % (index, len(case_ids), case_id))
            summary, charges, financials = client.case_bundle(case_id)
            case = {'id': case_id}
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

        if not cases:
            raise ValueError("no cases could be read")

        job.log("Building the CRS workbook...")
        path = build_workbook(cases, def_name, def_dob, is_lite)
        job.result = {
            "file": path,
            "def_name": def_name,
            "is_lite": is_lite,
            "failed_cases": failed,
        }

        if failed:
            job.log("%d of %d cases retrieved. These could not be read and are not in "
                    "the workbook: %s." % (len(cases), len(case_ids), ", ".join(failed)))
        else:
            job.log("Done. %d case%s written."
                    % (len(cases), "" if len(cases) == 1 else "s"))
        return "/job/%s/download" % job.id
    finally:
        icos_sessions.close(session_token)
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def build_workbook(cases, def_name, def_dob, is_lite):
    workbook = load_workbook('CRS Lite 3.5.5.xlsx' if is_lite else 'CRS 3.5.5.xlsx')
    sheet = workbook['CASE DATA']
    row = 4
    for case in cases:
        crs.process_case(case, sheet, row)
        row += 1

    sheet = workbook['BASIC INFO']
    sheet['B5'] = def_name.strip()
    sheet['B6'] = def_dob

    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)

    # def_name is user-supplied; sanitize it before using it in a filesystem path
    safe_name = secure_filename(def_name.strip().replace(' ', '_')) or "case"
    stamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    suffix = "_Lite_CRS_" if is_lite else "_CRS_"
    path = tmp_dir + safe_name + suffix + stamp + ".xlsx"
    workbook.save(path)
    return path


def download_name(def_name, is_lite):
    parts = [def_name.strip().replace(" ", "_")]
    if is_lite:
        parts.append("Lite")
    parts.append(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    return "%s.xlsx" % "_".join(parts)
