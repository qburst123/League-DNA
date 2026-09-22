"""Season replay with a strictly earlier-season reference pool.

The target season may be the ongoing (not yet finished) season: its replay then
stops at the season's finalized prefix and the following result simply stays
unrecorded until the collector finalizes it. The *reference* pool is unchanged
and still contains only seasons strictly earlier than the target season, so a
target's own later matchdays or later seasons can never leak into the evidence.
Reveal is historical only and never invents a result.
"""
from collections import defaultdict
import time


def team_vectors(rows):
    teams={}
    for row in rows:
        if row['status']!='final' or row['ft_home'] is None or row['ft_away'] is None:continue
        for name in (row['home'],row['away']):
            key=(row['season'],name)
            cards=teams.setdefault(key,[None]*30);i=row['day']-1
            if not 0<=i<30:continue
            if cards[i] is not None:cards[i]=False;continue
            cards[i]={'day':row['day'],'ft':f"{row['ft_home']}:{row['ft_away']}",
                      'home':row['home'],'away':row['away'],'match_id':row['id'],
                      'ht':f"{row['ht_home']}:{row['ht_away']}"}
    return teams


def following_evidence(pattern,references):
    n=len(pattern);occurrences=0;without_next=0;outcomes={};seasons=set();examples=[]
    for (season,team),cards in references.items():
        for start in range(31-n):
            segment=cards[start:start+n]
            if any(not card or card['ft']!=pattern[i] for i,card in enumerate(segment)):continue
            occurrences+=1;seasons.add(season)
            after=cards[start+n] if start+n<30 else None
            if not after:without_next+=1;continue
            outcome=outcomes.setdefault(after['ft'],{'match_ids':set(),'traces':0,'seasons':set()})
            outcome['match_ids'].add(after['match_id']);outcome['traces']+=1;outcome['seasons'].add(season)
            examples.append({'team':team,'season':season,'start':start+1,'end':start+n,'next_day':start+n+1,'context':segment,'next':after})
    matches=sum(len(row['match_ids']) for row in outcomes.values())
    result=[{'score':score,'matches':len(row['match_ids']),'traces':row['traces'],'seasons':len(row['seasons'])}
            for score,row in outcomes.items()]
    result.sort(key=lambda row:(-row['matches'],tuple(map(int,row['score'].split(':')))))
    examples.sort(key=lambda row:(-row['season'],row['start'],row['team']))
    return {'pattern':pattern,'length':n,'occurrences':occurrences,'context_seasons':len(seasons),
            'following_matches':matches,'following_traces':sum(r['traces'] for r in result),
            'without_recorded_followup':without_next,'outcomes':result,'examples':examples[:30]}


def replay_target_state(store,season):
    """Prefix and cutoff bounds of any replayable target season.

    The ongoing season is replayable up to its contiguous finalized prefix; a
    finished season keeps the original MD1–29 study range.
    """
    with store.lock:
        incoming=store.get_meta('upcoming',{}).get('season')
        prefix=store.prefix_length(season)
    prefix=min(prefix,30)
    complete=prefix>=30
    return {'season':season,'incoming_season':incoming,'prefix':prefix,'complete':complete,
            'state':'completed' if complete else 'ongoing',
            'max_cutoff':29 if complete else min(29,prefix)}


def replay_report(store,season,team,cutoff=4,length=None,reveal=False):
    began=time.perf_counter()
    with store.lock:
        incoming=store.get_meta('upcoming',{}).get('season')
        if incoming is None:raise ValueError('The collector has not published an incoming matchday yet, so no replay target can be chosen.')
        if season>incoming:raise ValueError('Future seasons have no recorded matches to replay. Choose the ongoing season or an earlier one.')
        state=replay_target_state(store,season)
        if state['max_cutoff']<1:raise ValueError('This season has no finalized matchday yet, so a replay cutoff cannot be chosen. It becomes replayable as soon as its first round is verified.')
        if not 1<=cutoff<=state['max_cutoff']:
            raise ValueError(f'The replay cutoff must be MD1–MD{state["max_cutoff"]} for this season (MD{state["max_cutoff"]} is its finalized prefix).')
        length=cutoff if length is None else length
        if not 1<=length<=cutoff:raise ValueError('Context length must be between 1 and the selected cutoff.')
        rows=store.all("SELECT * FROM matches WHERE season<=? AND status='final' ORDER BY season,day,id",(season,))
    target_rows=[r for r in rows if r['season']==season]
    target=team_vectors(target_rows).get((season,team))
    if target is None:raise ValueError('The selected team does not have a complete, unambiguous FT record in this season.')
    if any(not c for c in target[:cutoff]):
        raise ValueError(f'This team has no finalized FT record for every day through MD{cutoff}, so the replay context cannot be built without a gap or a conflict.')
    reference_rows=[r for r in rows if r['season']<season]
    references=team_vectors(reference_rows)
    allowed=sorted({r['season'] for r in reference_rows})
    context=[c['ft'] for c in target[cutoff-length:cutoff]]
    evidence=following_evidence(context,references)
    ladder=[]
    for n in range(cutoff,0,-1):
        result=following_evidence([c['ft'] for c in target[cutoff-n:cutoff]],references)
        ladder.append({k:result[k] for k in ('pattern','length','occurrences','following_matches','context_seasons')})
    following=target[cutoff] if cutoff<30 else None
    next_day=cutoff+1 if cutoff<30 else None
    actual=following if reveal and following else None
    return {'mode':'season-replay','target_state':state['state'],'season_prefix':state['prefix'],
            'season_complete':state['complete'],'max_cutoff':state['max_cutoff'],
            'target_season':season,'team':team,'cutoff':cutoff,'length':length,
            'context_start':cutoff-length+1,'visible_cards':target[:cutoff], 'following_day':next_day,
            'next_day_recorded':bool(following),'next_day_source':'final' if following else None,
            'actual_next':actual,'revealed':bool(reveal and following),'reference_seasons':allowed,
            'reference_matches':len(reference_rows),
            'reference_team_seasons':len(references),'evidence':evidence,'length_study':ladder,
            'reference_policy':'Only seasons strictly earlier than the selected target season are referenced, whether that season is ongoing or already finished.',
            'no_target_or_future_reference_data':all(item<season for item in allowed),
            'compute_ms':round((time.perf_counter()-began)*1000,2)}
