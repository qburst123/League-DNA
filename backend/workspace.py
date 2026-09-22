"""Data-first UI snapshot. Fixture names and actual FT records never depend on
Gold comparison success, an engine-version tag, or a materialized fingerprint.
"""
from __future__ import annotations
import base64
import json
import time
import uuid

from .forecast import cached_season_ledger

APP_VERSION='2.6.0'
SCHEMA='league-dna-workspace-1'


def resolved_fixtures(store, upcoming):
    season,day=upcoming.get('season'),upcoming.get('day')
    if not season or not day:return []
    rows=store.all('SELECT * FROM matches WHERE season=? AND day=? ORDER BY id',(season,day))
    ids=upcoming.get('event_ids') or []
    order={str(e):i for i,e in enumerate(ids)}
    rows.sort(key=lambda r:(order.get(str(r['event_id']),len(order)),r['id']))
    return rows


def build_workspace(store, include_receipts=True, record_trails=False):
    began=time.perf_counter()
    with store.lock:
        from .trail import observe_single_hits, trail_rows
        if record_trails:
            try:
                observe_single_hits(store)
            except Exception as error:
                # Trail tracking must never hide the incoming roster or FT data.
                store.log('trail',f'Trail capture deferred: {str(error)[:150]}','warning')
        trails=trail_rows(store)
        archive_id=store.get_meta('archive_id')
        if not archive_id:
            archive_id=str(uuid.uuid4());store.set_meta('archive_id',archive_id)
        overview=store.overview()
        upcoming=dict(store.get_meta('upcoming',{}))
        fixtures=resolved_fixtures(store,upcoming)
        season,day=upcoming.get('season'),upcoming.get('day') or 0
        rows=store.all('SELECT * FROM matches ORDER BY season,day,id')
        current=[r for r in rows if r['season']==season]
        closed={r['day'] for r in store.all("SELECT day FROM matchdays WHERE season=? AND status='complete'",(season or 0,))}
        prefix=min(store.prefix_length(season) if season else 0,max(0,day-1))
        shown_until=min(30,max(2,day-1))
        names=[];by_name={}
        for fixture in fixtures:
            for home in (True,False):
                name=fixture['home'] if home else fixture['away']
                if name in by_name:continue
                names.append(name)
                by_name[name]={'team':name,'opponent':fixture['away'] if home else fixture['home'],
                               'venue':'H' if home else 'A','event_id':fixture['event_id']}
        # An old database may have source-published names before fixture details
        # have been saved. These are retained source names, not invented teams.
        for name in (upcoming.get('teams',[]) if len(fixtures)<8 else []):
            if isinstance(name,str) and name and name not in by_name:
                names.append(name);by_name[name]={'team':name,'opponent':None,'venue':None,'event_id':None}
        roster=[]
        for name in names:
            by_day={}
            for r in current:
                if name in (r['home'],r['away']):by_day.setdefault(r['day'],[]).append(r)
            cards=[]
            for d in range(1,31):
                candidates=by_day.get(d,[])
                match=candidates[0] if len(candidates)==1 else None
                final=bool(match and match['status']=='final' and match['ft_home'] is not None and match['ft_away'] is not None)
                ft=f"{match['ft_home']}:{match['ft_away']}" if final else None
                live=f"{match['live_ft_home']}:{match['live_ft_away']}" if match and match['status']=='live' and not final and match['live_ft_home'] is not None and match['live_ft_away'] is not None else None
                state='final' if final else 'live' if live is not None else 'ambiguous' if len(candidates)>1 else 'not_played' if d>=day else 'missing'
                cards.append({'day':d,'ft':ft,'code':ft,'score':ft,'label':'CS' if final else '—',
                              'observed_score':live,'state':state,'round_closed':d in closed,
                              'comparison_eligible':final and d<=prefix,
                              'match_id':match['id'] if match else None,
                              'home':match['home'] if match else None,'away':match['away'] if match else None,
                              'ht':f"{match['ht_home']}:{match['ht_away']}" if final else None,
                              'venue':('H' if match['home']==name else 'A') if match else None})
            roster.append({**by_name[name],'season':season,'upcoming_day':day,'cards':cards,
                           'visible_until':shown_until,'known_final_scores':sum(c['state']=='final' and c['day']<day for c in cards),
                           'prefix_length':prefix})
        market_map={}
        for fixture in fixtures:
            market=store.market(fixture['event_id']) if fixture['event_id'] else None
            if market:
                market_map[str(fixture['event_id'])]={**market,'server_time':time.time()}
                fixture['markets']=[m for m in market['markets'] if m['name'].upper() in {'1X2','ODD/EVEN','BOTH TEAMS TO SCORE','DRAW NO BET','TOTAL'}]
                fixture['market_count']=len(market['markets']);fixture['markets_full']=bool(market['full']);fixture['odds_at']=market['fetched_at']
            else:fixture.update(markets=[],market_count=0,markets_full=False,odds_at=None)
        overview['upcoming']={**upcoming,'fixtures':fixtures,'teams':names}
        overview['current_season']=season
        overview['completed_prefix']=prefix
        now=time.time()
        receipts={}
        if include_receipts:
            wanted={r['final_source_hash'] or r['last_source_hash'] for r in rows if r['final_source_hash'] or r['last_source_hash']}
            wanted.update(r['source_hash'] for r in store.all('SELECT source_hash FROM single_hit_evidence'))
            for r in store.all('SELECT * FROM receipts'):
                if r['hash'] in wanted:
                    receipts[r['hash']]={k:v for k,v in r.items() if k!='body'}
                    receipts[r['hash']]['body_deflate']=base64.b64encode(r['body']).decode('ascii')
        health={'database':{'engine':'SQLite','journal':'WAL','integrity':store.db.execute('PRAGMA quick_check').fetchone()[0],
                            'file':'data/league.sqlite','size_bytes':store.db.execute('PRAGMA page_count').fetchone()[0]*store.db.execute('PRAGMA page_size').fetchone()[0],
                            'read_ms':round((time.perf_counter()-began)*1000,2)},
                'collector':{'source':'Betika public website feeds','upcoming_interval':10,'live_interval':5,'results_interval':18,'market_interval':60,'max_requests_per_second':1,'paused':overview['paused'],'read_only':True},
                'seasons':overview['seasons'],'endpoints':overview['endpoints'],'activity':store.all('SELECT * FROM activity ORDER BY id DESC LIMIT 80'),
                'discovery':store.get_meta('published_seasons',{}),'backfill':overview['backfill'],
                'limitations':['Teams and FT inputs are read directly from recorded fixtures and matches, independently of comparison results.',
                               'Historical matching does not predict an upcoming score. No wagering actions are provided.',
                               'Saved snapshots are not live feeds. A running collector and persistent storage are required for live collection.',
                               'Public season discovery uses the source rolling list; unknown periods can be unavailable after long outages.']}
        # Earlier-season continuation picks for the live FT board. Recomputed only
        # when finalized source FT data changes; never invented or filled in. A
        # ledger problem must never hide the roster, the FT cards or the results.
        forecast_error=None
        try:
            forecast=cached_season_ledger(store)
        except Exception as error:
            forecast_error=f'Continuation ledger deferred: {str(error)[:150]}'
            store.log('forecast',forecast_error,'warning')
            forecast=None
        return {'schema':SCHEMA,'archive_id':archive_id,'app_version':APP_VERSION,'captured_at':now,'revision':store.version,
                'results_revision':store.fp_revision,'overview':overview,'roster':roster,'results':rows,'forecast':forecast,
                'markets':market_map,'receipts':receipts,'health':health,
                'single_hit_rows':trails,'single_hit_tracker':{**store.get_meta('single_hit_tracker',{}),'enabled':True,'started_at':store.get_meta('single_hit_tracking_started')},
                'diagnostics':{'fixture_rows':len(fixtures),'roster_teams':len(roster),'source_published_team_names':len(upcoming.get('teams',[])),
                               'current_final_matches':sum(r['status']=='final' for r in current),'current_prefix':prefix,
                               'visible_until':shown_until,'source':'Recorded Betika fixtures and match rows','forecast_error':forecast_error,
                               'read_ms':round((time.perf_counter()-began)*1000,2)}}
