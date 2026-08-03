"""Run the room-and-board cases through reconcile_financials and see the columns."""
import glob, os, sys
from decimal import Decimal
REPO = '/home/alex/Napier3/.claude/worktrees/napier-real-fixture'
sys.path.insert(0, REPO); os.chdir(REPO)
os.environ['NAPIER_DISABLE_BACKGROUND'] = '1'
import case_parser, crs
DIRS = ['/home/alex/napier-icos-capture', '/home/alex/napier-icos-sweep3',
        '/home/alex/napier-icos-sweep4', '/home/alex/napier-icos-sweep5']

anyL = 0
for d in DIRS:
    for p in sorted(glob.glob(os.path.join(d, 'summary_*'))):
        stem = os.path.basename(p)[len('summary_'):-len('_real.html')]
        paths = {l: os.path.join(d, '%s_%s_real.html' % (l, stem))
                 for l in ('summary', 'charges', 'financials')}
        if not all(os.path.exists(x) for x in paths.values()):
            continue
        c = {'id': stem.replace('_', ' ')}
        try:
            for l, f in (('summary', case_parser.parse_case_summary),
                         ('charges', case_parser.parse_case_charges),
                         ('financials', case_parser.parse_case_financials)):
                f(open(paths[l], 'rb').read(), c)
        except Exception:
            continue
        cols, note = crs.reconcile_financials(c)
        if cols and cols.get('L'):
            anyL += 1
        rb = [l for l in (c.get('financials') or [])
              if 'ROOM/BOARD' in (l.get('detail') or '').upper()]
        if not rb:
            continue
        owed_rb = sum((crs._money(l.get('amount') or '0') - crs._money(l.get('paid') or '0')
                       for l in rb), Decimal('0'))
        print('%-4s due=%-10s rb_outstanding=$%-9s cols=%s'
              % (c['id'][7:11], c.get('total_due'), owed_rb,
                 {k: str(v) for k, v in (cols or {}).items()}))
        print('      note: %s' % ((note or '')[:150]))
print('\ncases anywhere in the 300 with a non-zero column L:', anyL)
