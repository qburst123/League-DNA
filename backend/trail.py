"""Persistent single-hit trail. Capture exact COUNT == 1 only; never forecast.

Rows are isolated by current team, current season and comparison scope. A row's
historical FT snapshot and alignment offset never change. Unbroken continuations
extend that row's captured band; a different/broken alignment adds a new row.
"""
from __future__ import annotations
import hashlib
import json
import time

TRACKER_VERSION = 'single-hit-trail-v1'


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def digest(value):
    return hashlib.sha256(compact(value).encode()).hexdigest()


def snapshot_cards(store,cards):
    result=[]
    for card in cards:
        saved=dict(card)
        if card.get('match_id'):
            match=store.one('SELECT final_source_hash FROM matches WHERE id=?',(card['match_id'],))
            if match and match['final_source_hash']:saved['source_hash']=match['final_source_hash']
        result.append(saved)
    return result


def continuous_with(last,full_cards,new_start,new_end,historical_team,historical_season,offset):
    if (last['historical_team'],last['historical_season'],last['alignment_offset']) != (historical_team,historical_season,offset):
        return False
    if new_end<last['current_end']:return False
    frozen=json.loads(last['data'])['historical_cards']
    for day in range(min(last['current_start'],new_start),new_end+1):
        historical_day=day-offset
        if day>len(full_cards) or historical_day<1 or historical_day>30:return False
        current=full_cards[day-1].get('ft')
        old=frozen[historical_day-1].get('ft')
        if current is None or old is None or current!=old:return False
    return True


def capture_result(store,result,observed_at=None,source_at=None):
    """Accept an internally computed Gold result. Not exposed as a client payload.

    Tests can use temporary stores. Production calls only observe_single_hits,
    which verifies source freshness and recomputes from actual stored records.
    """
    now=time.time() if observed_at is None else observed_at
    scope=result.get('scope','all')
    if scope not in ('all','same'):raise ValueError('Invalid single-hit scope')
    current_season=result.get('current_season')
    changed=inserted=extended=0
    with store.lock:
        store.db.execute('SAVEPOINT single_hit_capture')
        try:
            for team in result.get('teams',[]):
                if team.get('match_count')!=1 or not team.get('ready'):
                    continue
                candidates=team.get('candidates') or []
                if len(candidates)!=1:raise ValueError('A single-hit capture must have exactly one candidate')
                hit=candidates[0];start,end=team['current_start'],team['current_end']
                first,last=hit['start'],hit['end'];length=end-start+1
                full=team['full_cards'];current=full[start-1:end];historical=hit['cards'][first-1:last]
                if length<2 or len(current)!=length or len(historical)!=length or not 1<=start<=end<=30 or not 1<=first<=last<=30:
                    raise ValueError('Invalid captured matchday window')
                if hit['season']>=current_season:raise ValueError('Single-hit history must be an earlier season')
                if any(a.get('ft') is None or a.get('ft')!=b.get('ft') for a,b in zip(current,historical)):
                    raise ValueError('Captured score pairs must be exact and finalized')
                offset=start-first
                observation={'current_season':current_season,'current_team':team['team'],'scope':scope,
                             'current_start':start,'current_end':end,'historical_season':hit['season'],
                             'historical_team':hit['team'],'historical_start':first,'historical_end':last,
                             'scores':[c['ft'] for c in current]}
                identity=digest(observation)
                if store.one('SELECT observation_key FROM single_hit_observations WHERE observation_key=?',(identity,)):
                    continue
                previous=store.one('SELECT * FROM single_hit_rows WHERE current_season=? AND current_team=? AND scope=? ORDER BY sequence_no DESC LIMIT 1',
                                   (current_season,team['team'],scope))
                # A temporarily shorter/incomplete prefix is not a new forward
                # event in the forming season. Never replay older data as fresh.
                if previous and end<previous['current_end']:continue
                saved_current=snapshot_cards(store,full)
                if previous and continuous_with(previous,full,start,end,hit['team'],hit['season'],offset):
                    row_id=previous['id'];payload=json.loads(previous['data'])
                    payload['latest_current_cards']=saved_current
                    payload['last_observation_key']=identity
                    store.db.execute('UPDATE single_hit_rows SET current_start=?,current_end=?,last_seen=?,observation_count=observation_count+1,data=? WHERE id=?',
                                     (min(start,previous['current_start']),end,now,compact(payload),row_id))
                    saved_history=payload['historical_cards'];extended+=1
                else:
                    saved_history=snapshot_cards(store,hit['cards'])
                    payload={'historical_cards':saved_history,'first_current_cards':saved_current,'latest_current_cards':saved_current,
                             'first_observation_key':identity,'last_observation_key':identity,
                             'capture_reason':'first_single_hit' if previous is None else 'new_or_broken_alignment'}
                    number=previous['sequence_no']+1 if previous else 1
                    cursor=store.db.execute('''INSERT INTO single_hit_rows(current_season,current_team,scope,sequence_no,historical_season,historical_team,
                         alignment_offset,current_start,current_end,first_seen,last_seen,observation_count,data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                         (current_season,team['team'],scope,number,hit['season'],hit['team'],offset,start,end,now,now,1,compact(payload)))
                    row_id=cursor.lastrowid;inserted+=1
                evidence={c.get('source_hash') for c in saved_current+saved_history if c.get('source_hash')}
                store.db.executemany('INSERT OR IGNORE INTO single_hit_evidence VALUES(?,?)',[(row_id,h) for h in evidence])
                observation.update(match_count=1,full_length=team.get('full_length'),upcoming_day=result.get('upcoming_day'),
                                   comparison_revision=result.get('revision'),historical_blueprints=result.get('historical_blueprints'),
                                   source_observed_at=source_at,comparison_computed_at=result.get('computed_at'))
                store.db.execute('INSERT INTO single_hit_observations VALUES(?,?,?,?)',(identity,row_id,now,compact(observation)))
                changed+=1
            store.db.execute('RELEASE SAVEPOINT single_hit_capture')
        except Exception:
            store.db.execute('ROLLBACK TO SAVEPOINT single_hit_capture');store.db.execute('RELEASE SAVEPOINT single_hit_capture')
            raise
        if changed:store.bump()
    return {'observations':changed,'inserted':inserted,'extended':extended}


def observe_single_hits(store):
    """Live-only capture for both displayed historical scopes, all 16 teams.

    It never replays past days to invent historical uniqueness. Stale cached
    fixture states and incomplete first prefixes do not create trail captures.
    """
    from .gold import compare_correct_scores
    with store.lock:
        now=time.time();up=store.get_meta('upcoming',{})
        if not store.get_meta('single_hit_tracking_started'):
            store.set_meta('single_hit_tracking_started',now)
        fetched=up.get('fetched_at')
        age=max(0,now-(fetched or 0),now-(up.get('source_at') or fetched or 0))
        if not fetched or age>35:
            return {'status':'waiting_for_fresh_source','observations':0}
        prefix=store.prefix_length(up.get('season')) if up.get('season') else 0
        if (up.get('day') or 0)<3 or ((up.get('day') or 0)==3 and prefix<2):
            state={'version':TRACKER_VERSION,'status':'waiting_for_first_two_rounds','checked_at':now,
                   'current_season':up.get('season'),'upcoming_day':up.get('day'),'available_prefix':prefix,'observations':0}
            store.set_meta('single_hit_tracker',state)
            return state
        required=max(2,(up.get('day') or 1)-2)
        if prefix<required:
            # Rebuilding an old current prefix is not a new forward matchday
            # event. Wait until only the playing round can still be unclosed.
            state={'version':TRACKER_VERSION,'status':'waiting_for_current_backfill','checked_at':now,
                   'current_season':up.get('season'),'upcoming_day':up.get('day'),
                   'available_prefix':prefix,'required_prefix':required,'observations':0}
            store.set_meta('single_hit_tracker',state)
            return state
        marker=digest([TRACKER_VERSION,up.get('season'),up.get('day'),store.fp_revision,up.get('event_ids',[]),up.get('teams',[])])
        old=store.get_meta('single_hit_tracker',{})
        if old.get('marker')==marker:return {**old,'observations':0,'cached':True}
        stats={'observations':0,'inserted':0,'extended':0}
        for scope in ('all','same'):
            result=compare_correct_scores(store,scope)
            if len(result['teams'])!=16:continue
            captured=capture_result(store,result,now,up.get('source_at') or fetched)
            for key in stats:stats[key]+=captured[key]
        status={'version':TRACKER_VERSION,'status':'observing','marker':marker,'checked_at':now,
                'current_season':up.get('season'),'upcoming_day':up.get('day'),'revision':store.fp_revision,
                'scopes':['all','same'],**stats}
        store.set_meta('single_hit_tracker',status)
        if stats['inserted']:
            store.log('trail',f"Single-hit trail · {stats['inserted']} new aligned row(s), {stats['extended']} continuation(s)")
        return status


def trail_rows(store,season=None,team=None,scope=None):
    query='SELECT * FROM single_hit_rows WHERE 1=1';args=[]
    for name,value in [('current_season',season),('current_team',team),('scope',scope)]:
        if value is not None:query+=f' AND {name}=?';args.append(value)
    result=store.all(query+' ORDER BY current_season,current_team,scope,sequence_no',args)
    for row in result:
        row.update(json.loads(row.pop('data')))
        row['historical_start']=row['current_start']-row['alignment_offset']
        row['historical_end']=row['current_end']-row['alignment_offset']
        row['observations']=[{**json.loads(o['data']),'observation_key':o['observation_key'],'observed_at':o['observed_at']}
                             for o in store.all('SELECT * FROM single_hit_observations WHERE row_id=? ORDER BY observed_at,observation_key',(row['id'],))]
    return result
