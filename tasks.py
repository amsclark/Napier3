"""The work that runs inside background jobs.

Both tasks own an ICOS session end to end so it is always released: the search
task hands its live session to the store (the reaper closes it if the user
walks away), and the CRS task logs off when it finishes or fails.
"""

import datetime
import os
import platform
import zipfile

from openpyxl import load_workbook
from werkzeug.utils import secure_filename

import actions
import alerts
import case_parser
import crs
import grid
import icos_sessions
import roster
import statutes
from icos import IcosClient, IcosError, IcosStopped, STOPPED_MESSAGE

# One case ICOS will not hand over is a bad case: sealed, or a page the parser
# cannot read. Several in a row with nothing in between is the site being down,
# and there is no point walking the rest of the list to find that out one
# four-minute budget at a time. See Outage for why this is counted across the
# whole run and why it is six.
CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE = 6

# Searches are counted on their own and stay at three. A name that will not
# answer costs the 45 minute search budget rather than the four minute case
# budget, so three of them is already most of an afternoon, and unanswered
# names do not come in runs the way one client's sealed cases do.
SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE = 3

# When ICOS says it itself. A problem report page is the court site reporting
# that its own data source is unreachable, so a request that spent its entire
# budget being told that is not the ambiguous thing six exists to protect
# against. This is measured on the 2026-07-30 capture, where 45 case requests in
# a row came back as that page, byte for byte the same, while ICOS was
# degrading. A run that walks into that spends four minutes per case
# rediscovering what the first response already said in words, so under the
# ambiguous threshold it takes twenty three minutes to confirm, or ninety on the
# search side.
#
# Two rather than one, because a court site can serve one of these and come
# back, and the cost of being wrong is a clinic list that ends early. Sealed
# cases, stalls and timeouts cannot produce it at all, so the reason cases stop
# at six and names at three is untouched.
ICOS_DECLARED_ITSELF_DOWN_IS_AN_OUTAGE = 2


class Outage:
    """How many cases in a row Iowa Courts has refused, counted across a run.

    Reset by any case that comes back, so a run that is merely bumpy carries on
    to the end and only an unbroken run of refusals stops it.

    The count runs across the whole clinic list rather than per client. A per
    client counter starts over at every name, so a site that is down costs
    twenty clients times six cases times four minutes to discover twenty times
    over, and the staffer watches it happen. Shared, the run stops once.

    Six rather than three because three sealed cases in a row is something one
    real client can have, and that client's bad luck should not end the list
    for the nineteen names behind them. Six costs about twenty three minutes
    during a real outage, which is the price of not throwing away good runs.

    Refusals where ICOS reported its own outage are counted a second time on
    their own and stop the run at two, because that price is only worth paying
    while the cause is still in doubt.
    """

    def __init__(self, threshold=CASES_FAILED_IN_A_ROW_IS_AN_OUTAGE,
                 declared_threshold=ICOS_DECLARED_ITSELF_DOWN_IS_AN_OUTAGE):
        self.threshold = threshold
        self.declared_threshold = declared_threshold
        self.failures = 0
        self.declared = 0

    @property
    def over(self):
        return self.failures >= self.threshold or self.declared_down

    @property
    def declared_down(self):
        """The run stopped because ICOS said it was down, not because we guessed.

        Staff are told which of the two it was, since one of them means the
        account and the machine in front of them are fine.
        """
        return self.declared >= self.declared_threshold

    def worked(self):
        """A case came back. Whatever came before it was not an outage."""
        self.failures = 0
        self.declared = 0

    def failed(self, declared=False):
        """A case did not come back. True if that is now enough to stop.

        declared is ICOS having reported its own outage rather than having
        simply not answered.
        """
        self.failures += 1
        if declared:
            self.declared += 1
        return self.over


def stopped_responding(*counters, past=False):
    """How the run describes a dead site to staff, in half a sentence.

    Written once and used at every place a run says this, because a staffer
    reading that Iowa Courts reported its own system unavailable stops
    wondering whether it is their account, their password or their laptop, and
    that is the whole value of having told the two apart upstream.
    """
    if any(counter.declared_down for counter in counters):
        return ("Iowa Courts had reported that its own system was unavailable"
                if past else
                "Iowa Courts reported that its own system is unavailable")
    return "Iowa Courts had stopped responding" if past else \
        "Iowa Courts stopped responding"


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


def plan_searches(keys, case_dict, people, found_by=None):
    """The cases somebody picked, split by the search that has to precede them.

    Iowa Courts matches a name exactly as it is written on the case, so a client
    whose name is spelled two ways on the docket is two searches. The picking
    page pools both, and this puts the pooled selection back into the shape a
    pull needs: ICOS answers a case request out of the last result set it
    produced, so each spelling has to be standing in front of it before the
    cases that spelling found can be asked for.

    Deduplicated by case number across the whole selection. Two spellings can
    both turn up the same case -- a surname on its own and the full name always
    will -- and the same case written into the workbook twice doubles what the
    client owes, on every sheet, quietly.

    Groups that nobody picked anything out of are dropped, so a spelling that
    found only people who were somebody else costs no search.
    """
    people = list(people or [])
    found_by = found_by or {}
    groups = [[] for _ in people] or [[]]
    seen = set()
    for key in keys:
        index = found_by.get(key, 0)
        if not 0 <= index < len(groups):
            # A key the search job has no record of finding. It came off a
            # result that predates the alias support, or off a browser. Either
            # way the first search is the only one that can be repeated for it.
            index = 0
        for case_id in case_dict.get(key, []):
            if case_id in seen:
                continue
            seen.add(case_id)
            groups[index].append(case_id)
    return [{'person': people[index] if index < len(people) else None,
             'case_ids': case_ids}
            for index, case_ids in enumerate(groups) if case_ids]


def searches_behind(entry):
    """Every search that has to be repeated to pull this entry's cases.

    An entry recorded before Napier could search more than one spelling names
    its single search 'person' and lists its cases flat. One recorded since
    carries them already split. Both have to come back the same shape here,
    because a retry offered on a run from this morning is a retry of an entry
    in the old shape.
    """
    searches = entry.get('searches')
    if searches:
        return [{'person': group.get('person'),
                 'case_ids': list(group['case_ids'])} for group in searches]
    return [{'person': entry.get('person'),
             'case_ids': list(entry['case_ids'])}]


def _failed_by_search(entry):
    """Only what came back short, still split by the search behind it."""
    failed = set(entry['failed'])
    return [{'person': group['person'],
             'case_ids': [case_id for case_id in group['case_ids']
                          if case_id in failed]}
            for group in searches_behind(entry)]


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


# What the run says about a count whose adjudication wording is not in
# charge_code_map. The case is on the sheet under a guess, and the guess is OTH.
UNKNOWN_DISPOSITION_LINE = (
    "Iowa Courts recorded \"%s\" on %d case%s, which Napier does not "
    "recognise. Those rows are coded OTH and say so in the notes column: %s."
)

# What it says about the other row, which used to get the line above.
#
# Nothing about that was true of it. Its wording is not missing from
# charge_code_map, because charge_code_map is not what read it: the case has no
# adjudicated count at all, so the only disposition it carries is the status
# ICOS prints for the case as a whole, and case_level_code translates one
# wording of that vocabulary and refuses the rest on purpose. Nothing is coded
# OTH. Column G is empty, which BANKRUPTCY, EXEMPTIONS and SOL render as "open
# charge", so the row this line is about is being reported to the attorney as a
# charge still pending against a client whose case Iowa Courts has closed.
#
# Column V was taught the difference in August; this line was not, and it is the
# half Alex reads. It named a code the row does not carry and sent him to a
# notes column that says something else.
UNCODED_CASE_STATUS_LINE = (
    "Iowa Courts recorded \"%s\" as the status of %d case%s with no "
    "adjudicated count, which Napier does not translate into a CRS code. "
    "Column G is left empty, so the BANKRUPTCY, EXEMPTIONS and SOL sheets read "
    "%s as an open charge, and the notes column says so: %s."
)


def report_unknown_dispositions(job, unknown):
    """Say what a case was coded on, on the page and by email.

    Two things arrive here and they need telling apart. charge_code_map has a
    word for every outcome anyone has seen ICOS use, and anything else codes the
    case OTH, which is what the expungement, bankruptcy, exemption and licence
    sheets read as no conviction, so a client with a real conviction can come
    out of four sheets looking clean. The other is a case with nothing
    adjudicated on it, where column G is left empty rather than guessed at and
    three sheets read the empty cell as an open charge.

    Both leave the row saying so in column V, but the workbook goes to whoever
    runs the clinic. The map only gets a missing word added if it reaches Alex,
    and what an untranslated case status deserves is a question for Iowa Legal
    Aid, so the two go out under separate subjects rather than one.

    The wording and the case number go out. Both are court public record and
    neither is any use without the other: the word is what Napier could not act
    on and the case is the page to read it off. The client is not in it.
    """
    for (disposition, coded), case_ids in sorted(unknown.items()):
        count = len(case_ids)
        plural = "" if count == 1 else "s"
        listed = ", ".join(case_ids)
        if coded:
            job.log(UNKNOWN_DISPOSITION_LINE
                    % (disposition, count, plural, listed))
            alerts.record(job.id[:8], job.kind, alerts.UNKNOWN_DISPOSITION,
                          progress=alerts.recent_progress(job),
                          disposition=disposition,
                          cases=listed)
        else:
            job.log(UNCODED_CASE_STATUS_LINE
                    % (disposition, count, plural,
                       "that row" if count == 1 else "those rows", listed))
            alerts.record(job.id[:8], job.kind, alerts.UNCODED_CASE_STATUS,
                          progress=alerts.recent_progress(job),
                          **{'case status': disposition,
                             'cases': listed})
    return sorted({disposition for disposition, _ in unknown})


def search_task(job, username, password, people):
    """Search every spelling of one client's name, on one sign in.

    Iowa Courts matches the name exactly as it is written on the case, so a
    client whose docket spells them two ways is two searches and, until this,
    two runs, two turns with the shared account and two workbooks somebody
    merged by hand afterwards. The picking page already builds one workbook out
    of as many Iowa Courts identities as staff tick; all that was missing was
    being able to hand it more than one search.

    One spelling Iowa Courts will not answer does not cost the others, the same
    way one name does not cost a clinic list. It is recorded against that
    spelling and the results page says so. Nothing to show at all from any of
    them is still a failed run, which is what a single search that errored has
    always been.

    No name reaches the log. Progress lines are quoted into alert email and a
    client's name is privileged; which of N spellings is being searched is not.
    """
    client = IcosClient(log=job.log, alert=alerts.emitter(job))
    client.set_stop_check(lambda: job.cancelled)
    keep_session = False
    people = list(people)
    try:
        client.login(username, password)

        searches, case_dict, found_by = [], {}, {}
        first_error = None
        for index, person in enumerate(people):
            _stop_if_asked(job)
            if len(people) > 1:
                job.log("Searching Iowa Courts for spelling %d of %d..."
                        % (index + 1, len(people)),
                        count=index, total=len(people))
            record = {'name': roster.describe(person), 'keys': [],
                      'too_many_results': False, 'error': None}
            searches.append(record)
            try:
                body = client.search(person['first'], person['middle'],
                                     person['last'])
            except IcosStopped:
                raise
            except IcosError as e:
                record['error'] = e.message
                if first_error is None:
                    first_error = e
                continue

            job.log("Reading results...")
            cases, too_many_results = case_parser.parse_search(body)
            report_novel_roles(job, cases)
            found, keys = group_cases(cases)
            record['keys'] = keys
            record['too_many_results'] = too_many_results
            for key in keys:
                # Two spellings can return the same defendant, and always do
                # when one of them is the surname on its own. Merged rather than
                # listed twice, and deduplicated by case number inside the key,
                # because the picking page shows one row per defendant and the
                # count on it has to be the number of cases that row will pull.
                already = set(case_dict.setdefault(key, []))
                case_dict[key].extend(case_id for case_id in found[key]
                                      if case_id not in already)
                # First one wins. Either search can be put back in front of
                # ICOS to pull these cases, and the first is the one most likely
                # to be the fuller spelling staff typed first.
                found_by.setdefault(key, index)

        if searches and all(record['error'] for record in searches):
            # Every spelling refused. A run with nothing behind it is the
            # failure a single refused search has always been, and the message
            # ICOS gave is the useful half of it.
            raise first_error

        token = icos_sessions.put(client)
        keep_session = True
        keys = sorted(case_dict)
        total = sum(len(case_ids) for case_ids in case_dict.values())
        job.result = {
            "cases": case_dict,
            "keys": keys,
            "too_many_results": any(record['too_many_results']
                                    for record in searches),
            "session_token": token,
            # Kept because a CRS run that comes back short can only be retried
            # by putting this same search back in front of ICOS first, and by
            # then the run has logged off and the terms are gone. It never goes
            # to the browser: to_dict leaves result alone, and a client's name
            # is privileged.
            #
            # person is the first spelling and stays for the runs and the retry
            # payloads that only ever knew about one. people and found_by are
            # what a run with aliases needs: every spelling, and which of them
            # found each defendant.
            "person": people[0] if people else None,
            "people": people,
            "found_by": found_by,
            "searches": searches,
        }
        job.log("Found %d case%s across %d name%s."
                % (total, "" if total == 1 else "s",
                   len(keys), "" if len(keys) == 1 else "s"))
        return "/results/" + job.id
    finally:
        if not keep_session:
            client.logoff()
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def _stop_if_asked(job):
    """Raise if a staffer has asked this run to stop.

    Called between units of work. The retry loop has its own check, because a
    run is usually stopped precisely while it is waiting one out.
    """
    if getattr(job, 'cancelled', False):
        raise IcosStopped(STOPPED_MESSAGE)


def _pull_cases(job, client, case_ids, offset=0, total=None, outage=None):
    """Fetch and parse a list of cases. Returns what was read and what was not.

    Shared by the single-client run and the clinic list, so a case that Iowa
    Courts will not give up costs the same thing either way. offset and total
    are for the progress bar when this is one client's slice of a longer run.

    outage is the run's refusal count, passed in by a caller that has more than
    one client to get through so the whole list stops together. A caller with
    one client can leave it out and gets a counter of its own.
    """
    total = len(case_ids) if total is None else total
    outage = Outage() if outage is None else outage
    cases, failed = [], []
    not_attempted = []
    for index, case_id in enumerate(case_ids, start=1):
        _stop_if_asked(job)
        # Counted across the whole run, not this client's slice of it, so the
        # sentence and the bar under it never disagree on a clinic list.
        job.log("Pulling case %d of %d (%s)..." % (offset + index, total, case_id),
                count=offset + index - 1, total=total)
        case = {'id': case_id}
        try:
            summary, charges, financials = client.case_bundle(case_id)
        except IcosStopped:
            # A stop is an IcosError so it unwinds like one, which means every
            # handler that drops a case has to let it past or stopping a run
            # would read as one bad case and the run would carry on.
            raise
        except IcosError as e:
            # ICOS never gave up this case inside its retry budget. That
            # costs one row, not the run: the other cases are still worth
            # pulling and the workbook still gets built without this one.
            print("Case %s could not be retrieved: %r" % (case_id, e), flush=True)
            alerts.record(job.id[:8], job.kind, alerts.CASE_UNAVAILABLE,
                          progress=alerts.recent_progress(job),
                          case=case_id,
                          note="%s The run carried on without it." % e.message)
            failed.append(case_id)
            if outage.failed(declared=getattr(e, 'court_site_down', False)):
                not_attempted = case_ids[index:]
                break
            continue
        outage.worked()
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
        job.log("%s, so the last %d case%s could not be pulled. The workbook "
                "has the rest."
                % (stopped_responding(outage), len(not_attempted),
                   "" if len(not_attempted) == 1 else "s"))
    return cases, failed


def _retry_entry(def_name, def_dob, person, case_ids, cases, failed,
                 searches=None):
    """One client's share of what a retry needs.

    case_ids is everything that was asked for, in the order it was asked for,
    so a rebuilt workbook puts the recovered rows back where they were rather
    than tacked on the end. cases is what came back, kept whole: the retry
    signs in fresh and re-pulls only the failures, and it has to be able to
    write the ones that already worked without asking ICOS for them again.

    searches is the same case_ids split by the spelling that found them, for a
    client searched under more than one. Left out for a client searched once,
    where person carries the only search there is.

    These sit in the dyno's memory for the two hours the job lives, which is
    the same window the workbook itself sits in tmp for, so nothing is exposed
    here that the finished run was not already holding. They reach no log, no
    alert and no page.
    """
    entry = {'def_name': def_name, 'def_dob': def_dob, 'person': person,
             'case_ids': list(case_ids), 'cases': cases, 'failed': list(failed)}
    if searches:
        entry['searches'] = [{'person': group['person'],
                              'case_ids': list(group['case_ids'])}
                             for group in searches]
    return entry


def _retry_payload(kind, is_lite, entries):
    """What the finish page needs to offer another go, or None if it cannot.

    Every client of a clinic list is carried, not just the ones that came back
    short. A retry rebuilds the whole list and re-zips it, so a staffer who
    recovers one client's four cases still ends up with one file holding all
    twenty clients instead of having to keep two zips straight.

    None when nothing failed, and None when something failed that cannot be
    retried. ICOS decides which case a case request means from the last search
    it answered, so a client whose search terms were never recorded cannot be
    re-selected, and pulling their cases without re-selecting is exactly the
    bug that made every case after the first client come back as a stub.
    """
    if not any(entry['failed'] for entry in entries):
        return None
    if any(group['case_ids'] and not group['person']
           for entry in entries for group in _failed_by_search(entry)):
        return None
    return {'kind': kind, 'is_lite': is_lite, 'clients': entries}


def _merged_cases(entry, recovered):
    """Last run's cases plus this run's, back in the order they were asked for.

    Keyed by case number so a case that failed the first time and worked the
    second appears once, in its own row, rather than twice or at the end.
    """
    by_id = {case['id']: case for case in entry['cases']}
    by_id.update({case['id']: case for case in recovered})
    return [by_id[case_id] for case_id in entry['case_ids'] if case_id in by_id]


def batch_search_task(job, username, password, people, rejected=()):
    """Search a whole clinic list on one sign in.

    One search per name, all inside the session the first login opened, and the
    session is handed to the store at the end the same way a single search
    hands it over. What this saves is not the searching, it is the queueing:
    Iowa Courts allows one session per account and Iowa Legal Aid shares a few,
    so twenty clients used to mean twenty sign ins competing for the same
    account, and any two staff working the list at once locked each other out.

    One name Iowa Courts will not answer for does not end the list. It is
    recorded against that client and the run carries on, because the other
    nineteen clients are still in the building.

    No client's name is logged. The progress log is quoted into alert email and
    those names are privileged; a position in the list is not.

    rejected is the lines Napier could not read a name out of, carried here
    rather than in the browser's session for the same reason: they can hold a
    piece of a client's name, and the session cookie is a store on a machine
    other people use. This lives in the dyno's memory and is gone in two hours.
    """
    client = IcosClient(log=job.log, alert=alerts.emitter(job))
    client.set_stop_check(lambda: job.cancelled)
    keep_session = False
    try:
        client.login(username, password)

        clients = []
        # A counter rather than an int, so a name refused because ICOS said it
        # was down stops the list at two. A name costs the 45 minute search
        # budget, so the third one is an hour and a half spent confirming what
        # the first refusal already said in words.
        dead_searches = Outage(threshold=SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE)
        for index, person in enumerate(people, start=1):
            _stop_if_asked(job)
            job.log("Searching Iowa Courts for name %d of %d..."
                    % (index, len(people)), count=index - 1, total=len(people))
            # The search terms ride along because the CRS run has to repeat
            # this exact search before it can pull this client's cases. See
            # batch_crs_task.
            entry = {'name': roster.describe(person), 'keys': [], 'cases': {},
                     'too_many_results': False, 'error': None,
                     'person': person, 'people': roster.spellings(person),
                     'found_by': {}}
            clients.append(entry)
            # One client, one or more spellings. A clinic list carries an "aka"
            # for the same reason a single search does: Iowa Courts matches the
            # name as it is written on the case, and a client whose docket
            # spells them two ways is otherwise two lines on the list and two
            # workbooks somebody merges afterwards.
            stop_the_list = False
            for spelling, alias in enumerate(entry['people']):
                try:
                    body = client.search(alias['first'], alias['middle'],
                                         alias['last'])
                except IcosStopped:
                    raise
                except IcosError as e:
                    # Recorded once per client. Which spelling refused is not
                    # worth a second message to somebody looking at a list of
                    # twenty, and the counter is what decides whether the site
                    # is down.
                    entry['error'] = e.message
                    if dead_searches.failed(
                            declared=getattr(e, 'court_site_down', False)):
                        for skipped in people[index:]:
                            clients.append({
                                'name': roster.describe(skipped), 'keys': [],
                                'cases': {}, 'too_many_results': False,
                                'error': "%s before Napier reached this name."
                                         % stopped_responding(dead_searches,
                                                              past=True)})
                        job.log("%s, so the rest of the list was not searched."
                                % stopped_responding(dead_searches))
                        stop_the_list = True
                    break
                dead_searches.worked()
                cases, too_many = case_parser.parse_search(body)
                report_novel_roles(job, cases)
                found, keys = group_cases(cases)
                for key in keys:
                    already = set(entry['cases'].setdefault(key, []))
                    entry['cases'][key].extend(
                        case_id for case_id in found[key]
                        if case_id not in already)
                    entry['found_by'].setdefault(key, spelling)
                entry['too_many_results'] = (entry['too_many_results']
                                             or too_many)
            entry['keys'] = sorted(entry['cases'])
            if entry['keys']:
                # A spelling that answered is a client Napier can still build,
                # so a second one that refused is not worth showing as this
                # client's outcome.
                entry['error'] = None
            if stop_the_list:
                break

        token = icos_sessions.put(client)
        keep_session = True
        job.result = {"clients": clients, "session_token": token,
                      "rejected": list(rejected)}
        answered = sum(1 for entry in clients if entry['keys'])
        job.log("Searched %d name%s. %d came back with cases."
                % (len(clients), "" if len(clients) == 1 else "s", answered))
        return "/roster/" + job.id
    finally:
        if not keep_session:
            client.logoff()
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def _reselect(job, client, pick):
    """Put this client's search results back in front of ICOS.

    Answers (whether it took, whether ICOS reported its own outage), because
    the caller's dead-search counter believes the second one sooner than it
    believes a name that merely went unanswered.

    Nothing is read out of the response. The point is the side effect: ICOS
    decides which case a case request means from whatever it answered last,
    so the run has to be standing on this client's results before it asks for
    this client's cases.

    A name that will not answer twice costs that client and not the list, and
    it is reported as a search failure rather than as sixty-odd unavailable
    cases, because that is what it is.
    """
    person = pick.get('person')
    if not person:
        return False, False
    try:
        client.search(person['first'], person['middle'], person['last'])
    except IcosStopped:
        raise
    except IcosError as e:
        alerts.record(job.id[:8], job.kind, alerts.CASE_UNAVAILABLE,
                      progress=alerts.recent_progress(job),
                      case="(re-search before pulling a client's cases)",
                      note="%s That client was skipped." % e.message)
        return False, getattr(e, 'court_site_down', False)
    return True, False


def _pull_grouped(job, client, searches, reselect, offset=0, total=None,
                  outage=None, dead_searches=None):
    """Pull one client's cases a spelling at a time.

    ICOS decides which case a case request means from the last search it
    answered, so a client found under two spellings cannot have both halves
    pulled off one result set. Each group gets its own spelling put back in
    front of ICOS first, which is one request and about a third of a second,
    against a stub case that fails every validator and burns the four minute
    case budget finding out.

    reselect is False for the one case where the search is already standing
    there: a single-spelling run pulling straight off the search that produced
    it. Making that path do a redundant search would spend a request and a
    lock on the shared account on every ordinary run to serve the rare one.

    Answers (cases, failed, whether any spelling would not answer twice). The
    last of those is what separates "Iowa Courts would not give up these cases"
    from "Iowa Courts would not answer this name", which are different things
    to tell somebody and were the same message before aliases existed.
    """
    outage = Outage() if outage is None else outage
    dead_searches = (Outage(threshold=SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE)
                     if dead_searches is None else dead_searches)
    wanted = [group for group in searches if group['case_ids']]
    if total is None:
        total = offset + sum(len(group['case_ids']) for group in wanted)

    cases, failed, refused_a_name = [], [], False
    pulled = offset
    for index, group in enumerate(wanted, start=1):
        _stop_if_asked(job)
        case_ids = group['case_ids']
        if outage.over or dead_searches.over:
            failed.extend(case_ids)
            pulled += len(case_ids)
            continue
        if reselect:
            if len(wanted) > 1:
                job.log("Spelling %d of %d: pulling %d case%s..."
                        % (index, len(wanted), len(case_ids),
                           "" if len(case_ids) == 1 else "s"),
                        count=pulled, total=total)
            reselected, site_down = _reselect(job, client, group)
            if not reselected:
                dead_searches.failed(declared=site_down)
                refused_a_name = True
                failed.extend(case_ids)
                pulled += len(case_ids)
                continue
            dead_searches.worked()
        got, missed = _pull_cases(job, client, case_ids, offset=pulled,
                                  total=total, outage=outage)
        cases.extend(got)
        failed.extend(missed)
        pulled += len(case_ids)
    return cases, failed, refused_a_name


def batch_crs_task(job, session_token, picks, is_lite):
    """Build a workbook per client, all on the sign in the batch search opened.

    picks is what came back off the roster page, already checked against the
    search job by the route: name, date of birth, the defendant keys somebody
    ticked, and that client's own case list.

    Each client's search is repeated here, immediately before their cases are
    pulled. ICOS keys case selection to the most recent set of search results,
    not to the case number asked for, so on a clinic list every case belonging
    to anyone but the last name searched comes back as a stub: right heading,
    no charges, no money. The validators refuse it and it retries for the full
    case budget, which reads on the progress page as a hang and costs four
    minutes a case. Re-searching costs one request and about a third of a
    second per client.

    One client's run failing costs that client's workbook and nothing else. The
    clinic gets the rest and the finish page says which one is missing, because
    a list that quietly comes back one short is worse than one that says so.
    """
    client = icos_sessions.claim(session_token)
    if client is None:
        raise IcosError("That clinic list is no longer signed in to Iowa "
                        "Courts Online. Please run it again.")
    client.set_alert(alerts.emitter(job))
    # Both, or the person waiting watches a job that has stopped being written
    # to while the run carries on under the search job's name.
    client.set_log(job.log)
    client.set_stop_check(lambda: job.cancelled)

    total_cases = sum(len(pick['case_ids']) for pick in picks)
    try:
        built = []
        retry_entries = []
        pulled = 0
        # One count for the whole list. Six cases refused in a row is Iowa
        # Courts being down, and the nineteen names behind this one are not
        # going to fare any better.
        outage = Outage()
        # The same news arriving by the other door, and it arrives first: a
        # client's turn starts with the re-search, so a site that is properly
        # down never lets the case count get going at all. Counted at three
        # like the search task's own, and kept apart from the case count
        # because the two budgets are nothing like each other.
        dead_searches = Outage(threshold=SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE)
        announced = False
        for index, pick in enumerate(picks, start=1):
            _stop_if_asked(job)
            record = {'name': pick['def_name'], 'requested': len(pick['case_ids']),
                      'written': 0, 'failed': [], 'file': None, 'error': None}
            built.append(record)
            # Carried alongside the record rather than inside it, because this
            # holds whole parsed cases and the record is what the finish page
            # renders.
            entry = _retry_entry(pick['def_name'], pick['def_dob'],
                                 pick.get('person'), pick['case_ids'], [], [],
                                 pick.get('searches'))
            retry_entries.append(entry)

            # Before the re-search, not after it. A dead site would otherwise
            # still be asked to answer every remaining name, and a search
            # carries the 45 minute search budget, which is ten times what
            # skipping the cases behind it just saved.
            if outage.over or dead_searches.over:
                if not announced:
                    announced = True
                    job.log("%s, so the rest of the list was not pulled."
                            % stopped_responding(outage, dead_searches))
                pulled += len(pick['case_ids'])
                record['failed'] = list(pick['case_ids'])
                entry['failed'] = list(pick['case_ids'])
                record['error'] = ("%s before Napier reached this client, so "
                                   "their cases were not pulled."
                                   % stopped_responding(outage, dead_searches,
                                                        past=True))
                continue

            job.log("Client %d of %d: pulling %d case%s..."
                    % (index, len(picks), len(pick['case_ids']),
                       "" if len(pick['case_ids']) == 1 else "s"),
                    count=pulled, total=total_cases)
            # A spelling at a time, each one put back in front of ICOS before
            # its own share is asked for. A client on the list with no aka is
            # one group and one re-search, which is what this always did.
            cases, failed, refused_a_name = _pull_grouped(
                job, client, searches_behind(pick), reselect=True,
                offset=pulled, total=total_cases, outage=outage,
                dead_searches=dead_searches)
            pulled += len(pick['case_ids'])
            record['failed'] = failed
            entry['cases'], entry['failed'] = cases, list(failed)
            if not cases:
                record['error'] = (
                    "Iowa Courts would not answer this client's name a second "
                    "time, so their cases could not be pulled. Search them on "
                    "their own."
                    if refused_a_name else
                    "Iowa Courts would not give up any of this client's "
                    "cases, so there is no workbook for them.")
                continue
            try:
                path, unknown, atp = build_workbook(cases, pick['def_name'],
                                                    pick['def_dob'], is_lite,
                                                    failed)
            except Exception as e:
                # The other clients' workbooks are already built or still to
                # come, and neither should be lost to this one.
                print("Workbook failed for client %d: %r" % (index, e), flush=True)
                alerts.record(job.id[:8], job.kind, alerts.JOB_FAILED,
                              progress=alerts.recent_progress(job),
                              **{'note': "One client of %d in a clinic list. "
                                         "The rest of the list carried on."
                                         % len(picks),
                                 'traceback': alerts.safe_traceback(e)})
                record['error'] = ("Napier could not build this client's "
                                   "workbook. The rest of the list is here.")
                continue
            report_unknown_dispositions(job, unknown)
            record['written'] = len(cases)
            record['file'] = path
            record['atp'] = atp

        if not any(record['file'] for record in built):
            raise ValueError("no workbooks could be built")

        job.log("Packaging %d workbook%s..."
                % (len(built), "" if len(built) == 1 else "s"),
                count=total_cases, total=total_cases)
        bundle = _zip_workbooks(built, is_lite)
        job.result = {
            "file": bundle,
            "is_lite": is_lite,
            "clients": built,
            "written_cases": sum(record['written'] for record in built),
            "requested_cases": total_cases,
            "def_name": "a clinic list of %d client%s"
                        % (len(built), "" if len(built) == 1 else "s"),
            "done_url": "/batch-done/%s" % job.id,
            "retry": _retry_payload('batch_crs', is_lite, retry_entries),
        }
        job.log("Done. %d of %d workbook%s built."
                % (sum(1 for record in built if record['file']), len(built),
                   "" if len(built) == 1 else "s"))
        return "/batch-done/%s" % job.id
    finally:
        client.logoff()
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def _zip_workbooks(built, is_lite):
    """One file to download, because a clinic list is one errand.

    Every workbook is also kept on disk under its own name and served on its
    own from the finish page, for the staffer who only wants the one client
    they are about to see.
    """
    stamp = crs.iowa_now().strftime('%Y%m%d%H%M%S')
    path = tmp_dir + "Napier_clinic_list_" + stamp + ".zip"
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for position, record in enumerate(built, start=1):
            if not record['file']:
                continue
            # Numbered because two clients on one clinic list can share a name
            # and a zip that quietly holds one of them is the kind of thing
            # nobody notices until the wrong person is sitting there.
            name = secure_filename(download_name(record['name'], is_lite))
            bundle.write(record['file'],
                         "%02d_%s" % (position, name or "client.xlsx"))
    return path


def crs_task(job, session_token, keys, case_dict, def_name, def_dob, is_lite,
             person=None, people=None, found_by=None):
    """person is the search that produced these cases, carried for the retry.

    Optional because a job that started before the search job recorded it has
    none, and a run with no way back is still a run worth finishing. The finish
    page just does not offer another go.

    people and found_by are what a client searched under more than one spelling
    needs: every spelling, and which of them found each defendant somebody
    ticked. Absent, this is one search and behaves exactly as it always has,
    pulling straight off the result set already in front of ICOS.
    """
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
    # Without this the retry notices go on being written into the search job,
    # and the progress page the staffer is actually watching sits on one case
    # with nothing under it while ICOS is being retried.
    client.set_log(job.log)
    client.set_stop_check(lambda: job.cancelled)

    try:
        searches = plan_searches(keys, case_dict, people or [person], found_by)
        # The order the groups are pulled in, so the workbook's rows and the
        # retry's idea of where a recovered row goes back agree with each other.
        case_ids = [case_id for group in searches
                    for case_id in group['case_ids']]

        # Only when there is more than one, because a single search is already
        # the last thing ICOS answered and re-running it would cost every
        # ordinary run a request to serve the rare one.
        cases, failed, refused_a_name = _pull_grouped(
            job, client, searches, reselect=len(searches) > 1)

        if not cases:
            if refused_a_name:
                raise IcosError("Iowa Courts would not answer that name a "
                                "second time, so none of the cases could be "
                                "pulled. Please try the search again.")
            raise ValueError("no cases could be read")

        job.log("Building the CRS workbook...", count=len(case_ids), total=len(case_ids))
        path, unknown_dispositions, atp = build_workbook(cases, def_name,
                                                         def_dob, is_lite,
                                                         failed)
        report_unknown_dispositions(job, unknown_dispositions)
        job.result = {
            "file": path,
            "atp": atp,
            "def_name": def_name,
            "is_lite": is_lite,
            "written_cases": len(cases),
            "requested_cases": len(case_ids),
            "failed_cases": failed,
            "done_url": "/done/%s" % job.id,
            "retry": _retry_payload('crs', is_lite, [
                _retry_entry(def_name, def_dob, person, case_ids, cases,
                             failed, searches)]),
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


def retry_task(job, username, password, payload):
    """Another go at just the cases Iowa Courts would not give up.

    Until this, a run that came back four cases short left staff two choices:
    look those cases up on Iowa Courts by hand, or run the whole thing again
    and spend another twenty minutes and another turn with the shared account
    to re-pull sixty-three cases that already worked. Neither is a good day.
    The July run that dropped sixty-three cases had no third option at all.

    This signs in fresh, because the run that failed logged its session off on
    the way out and that is the behaviour keeping the shared account usable. It
    then does per client what the clinic list does: put that client's search
    back in front of ICOS, pull only their failures, and write a workbook from
    what came back plus what came back last time.

    Every client of a clinic list is rebuilt, including the ones that had no
    failures, so the retry produces one complete zip rather than a second
    partial one to keep straight alongside the first.

    A retry that still comes back short can itself be retried, because it
    leaves the same thing behind that it was started from.
    """
    client = IcosClient(log=job.log, alert=alerts.emitter(job))
    client.set_stop_check(lambda: job.cancelled)
    entries = payload['clients']
    is_lite = payload['is_lite']
    total = sum(len(entry['failed']) for entry in entries)
    try:
        client.login(username, password)

        built, rebuilt, pulled, recovered_total = [], [], 0, 0
        # Shared for the same reason the clinic list shares one, and it matters
        # more here: a retry is somebody's second try, and spending it walking
        # a dead site client by client is how they stop bothering with the
        # button at all.
        outage = Outage()
        dead_searches = Outage(threshold=SEARCHES_FAILED_IN_A_ROW_IS_AN_OUTAGE)
        announced = False
        for index, entry in enumerate(entries, start=1):
            _stop_if_asked(job)
            record = {'name': entry['def_name'],
                      'requested': len(entry['case_ids']),
                      'written': 0, 'failed': [], 'file': None, 'error': None}
            built.append(record)

            recovered, still_failed, reselect_failed = [], list(entry['failed']), False
            skipped = False
            if entry['failed'] and (outage.over or dead_searches.over):
                # Their earlier cases are still rebuilt below. It is only the
                # second attempt at the missing ones that is called off.
                skipped = True
                if not announced:
                    announced = True
                    job.log("%s, so the rest of the missing cases were not "
                            "tried again."
                            % stopped_responding(outage, dead_searches))
                pulled += len(entry['failed'])
            elif entry['failed']:
                job.log("Client %d of %d: trying %d case%s again..."
                        % (index, len(entries), len(entry['failed']),
                           "" if len(entry['failed']) == 1 else "s"),
                        count=pulled, total=total)
                # Split by the spelling that found each of them, so a client
                # searched under two names has each one put back in front of
                # ICOS before its own share is asked for. One spelling is the
                # ordinary case and comes through here as a single group.
                recovered, still_failed, reselect_failed = _pull_grouped(
                    job, client, _failed_by_search(entry), reselect=True,
                    offset=pulled, total=total, outage=outage,
                    dead_searches=dead_searches)
                pulled += len(entry['failed'])
                recovered_total += len(recovered)

            # Everything that has ever come back for this client, in the order
            # it was asked for, so the rebuilt workbook is the whole summary
            # and not a supplement to one.
            cases = _merged_cases(entry, recovered)
            record['failed'] = still_failed
            # entry.get('searches') and not searches_behind(), so a client
            # searched once is written back in the shape it arrived in rather
            # than growing a one-group split it never needed.
            rebuilt.append(_retry_entry(entry['def_name'], entry['def_dob'],
                                        entry['person'], entry['case_ids'],
                                        cases, still_failed,
                                        entry.get('searches')))
            if not cases:
                if skipped:
                    record['error'] = ("%s before Napier reached this client, "
                                       "so there is still no workbook for them."
                                       % stopped_responding(outage,
                                                            dead_searches,
                                                            past=True))
                elif reselect_failed:
                    record['error'] = ("Iowa Courts would not answer this "
                                       "client's name, so there is still no "
                                       "workbook for them.")
                else:
                    record['error'] = ("Iowa Courts still would not give up any "
                                       "of this client's cases, so there is "
                                       "still no workbook for them.")
                continue
            try:
                path, unknown, atp = build_workbook(cases, entry['def_name'],
                                                    entry['def_dob'], is_lite,
                                                    still_failed)
            except Exception as e:
                print("Workbook failed on retry for client %d: %r" % (index, e),
                      flush=True)
                alerts.record(job.id[:8], job.kind, alerts.JOB_FAILED,
                              progress=alerts.recent_progress(job),
                              **{'note': "Rebuilding one client of %d on a "
                                         "retry. The rest carried on."
                                         % len(entries),
                                 'traceback': alerts.safe_traceback(e)})
                record['error'] = ("Napier could not rebuild this client's "
                                   "workbook. The earlier one is still on the "
                                   "run you started this from.")
                continue
            # Only the cases that came back this time. The rest were reported
            # when they were first read, and telling somebody twice about a
            # disposition they have already added to the map is how alerting
            # stops being read.
            fresh_ids = {case['id'] for case in recovered}
            report_unknown_dispositions(job, {
                key: [case_id for case_id in case_ids if case_id in fresh_ids]
                for key, case_ids in unknown.items()
                if any(case_id in fresh_ids for case_id in case_ids)})
            record['written'] = len(cases)
            record['file'] = path
            record['atp'] = atp

        if not any(record['file'] for record in built):
            raise ValueError("no workbooks could be rebuilt")

        still_missing = sum(len(record['failed']) for record in built)
        job.log("Recovered %d of %d case%s. %s"
                % (recovered_total, total, "" if total == 1 else "s",
                   "Iowa Courts still would not give up %d." % still_missing
                   if still_missing else "Nothing is missing now."))

        after = _retry_payload(payload['kind'], is_lite, rebuilt)
        if payload['kind'] == 'batch_crs':
            job.result = {
                "file": _zip_workbooks(built, is_lite),
                "is_lite": is_lite,
                "clients": built,
                "written_cases": sum(record['written'] for record in built),
                "requested_cases": sum(record['requested'] for record in built),
                "def_name": "a clinic list of %d client%s"
                            % (len(built), "" if len(built) == 1 else "s"),
                "done_url": "/batch-done/%s" % job.id,
                "retry": after,
            }
            return "/batch-done/%s" % job.id

        record = built[0]
        job.result = {
            "file": record['file'],
            # Rebuilt from every case that has ever come back for this client,
            # so a retry that recovered two cases raises the balance the
            # calculator is about to be given.
            "atp": record.get('atp'),
            "def_name": record['name'],
            "is_lite": is_lite,
            "written_cases": record['written'],
            "requested_cases": record['requested'],
            "failed_cases": record['failed'],
            "done_url": "/done/%s" % job.id,
            "retry": after,
        }
        return "/done/%s" % job.id
    finally:
        client.logoff()
        alerts.digest(job.id[:8], job.kind, alerts.recent_progress(job))


def build_workbook(cases, def_name, def_dob, is_lite, failed=()):
    """Returns the path written, the dispositions Napier could not read, and
    the two figures the ability-to-pay calculator asks for.

    failed is the cases that would not come off Iowa Courts, so the workbook
    can say what is not in it. The file travels further than any page Napier
    serves, and one that is quietly short two cases is worse than one that
    says it is short two cases.

    The second value maps the ICOS wording, paired with whether the row it
    landed on came out with a code in column G, to the case numbers it turned up
    on. Empty on almost every run. When it is not, those cases are either on the
    sheet under a guessed code or on it with no code at all, and somebody needs
    to know which before the workbook is used, so the caller reports it rather
    than the file quietly carrying it.
    """
    workbook = load_workbook('CRS Lite 3.5.5.xlsx' if is_lite else 'CRS 3.5.5.xlsx')
    sheet = workbook['CASE DATA']
    # One clinic date for the whole workbook, read once. Column I asks whether a
    # probation term is still running, which is only answerable against a day,
    # and it has to be the same day BASIC INFO B3 gets below or the workbook
    # disagrees with itself about when it was built.
    clinic_date = crs.iowa_today()
    row = 4
    unknown = {}
    for case in cases:
        for key in crs.process_case(case, sheet, row, clinic_date) or []:
            unknown.setdefault(key, []).append(case['id'])
        row += 1

    # Columns W to AH take column F apart one statute per column, and the
    # expungement sheet's second screen reads them with LEFT(). The template
    # did the split with an array formula that pads its unused slots with
    # #VALUE!, which LEFT() will not step over, so the 910.7 columns failed on
    # exactly the rows that cleared the first screen and nowhere else. Napier
    # wrote column F, so it can split it too, and an empty slot is an empty
    # string rather than an error.
    for case_row, spare in statutes.write_statute_split(workbook,
                                                        row - 4).items():
        crs.append_note(sheet, case_row,
                        statutes.OVERFLOW_NOTE % ", ".join(spare))

    # The templates were filled down by hand, each to a different depth, and
    # nothing above stops the case list before it runs past them. A case on a
    # row no derived sheet reaches is not reported as time barred, not reported
    # as having no argument and not counted as a pending charge: it is simply
    # absent, and the workbook says nothing. This fills the author's own last
    # row down to the case list and widens the totals to match. It changes no
    # formula's meaning, and does nothing at all to a workbook that fits.
    grid.extend_formula_grid(workbook, row - 4)

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
    atp = actions.build_action_sheet(workbook, cases, row - 4, clinic_date,
                                     def_name.strip(), failed)

    if not os.path.exists(tmp_dir):
        os.mkdir(tmp_dir)

    # def_name is user-supplied; sanitize it before using it in a filesystem path
    safe_name = secure_filename(def_name.strip().replace(' ', '_')) or "case"
    stamp = crs.iowa_now().strftime('%Y%m%d%H%M%S')
    suffix = "_Lite_CRS_" if is_lite else "_CRS_"
    path = tmp_dir + safe_name + suffix + stamp + ".xlsx"
    workbook.save(path)
    return path, unknown, atp


def download_name(def_name, is_lite):
    parts = [def_name.strip().replace(" ", "_")]
    if is_lite:
        parts.append("Lite")
    parts.append(crs.iowa_now().strftime("%Y%m%d_%H%M%S"))
    return "%s.xlsx" % "_".join(parts)
