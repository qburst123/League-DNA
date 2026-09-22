"""Sparse index of observed FT contexts and recorded following outcomes.

Descriptive archive statistics only. No fixture forecasts or wagering picks.
Every context stays inside one team-season and never bridges an unknown day.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import threading
import time
import zlib

OUTCOME=struct.Struct('>HIII')  # next-score code, distinct next matches, traces, seasons
INDEX_VERSION=1
SCHEMA='''
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA wal_autocheckpoint=256;
PRAGMA journal_size_limit=4194304;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scores(code INTEGER PRIMARY KEY,score TEXT UNIQUE,home INTEGER,away INTEGER,matches INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS patterns(context BLOB PRIMARY KEY,order_len INTEGER NOT NULL,occurrences INTEGER NOT NULL,seasons INTEGER NOT NULL,outcomes BLOB NOT NULL) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS pattern_order ON patterns(order_len,occurrences DESC);
CREATE TABLE IF NOT EXISTS partitions(season INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL,counts BLOB NOT NULL);
PRAGMA user_version=1;
'''


def score_code(home,away):
    if not isinstance(home,int) or not isinstance(away,int) or not 0<=home<=99 or not 0<=away<=99:
        raise ValueError('FT goals must be integers between 0 and 99')
    return 1+home*100+away


def score_text(code):
    code-=1
    return f'{code//100}:{code%100}'


def encode_pattern(values):
    if not 1<=len(values)<=30:raise ValueError('Choose 1 to 30 FT results')
    codes=[]
    for value in values:
        if not isinstance(value,str) or not re.fullmatch(r'\d{1,2}:\d{1,2}',value):
            raise ValueError('Use literal home:away scores such as 0:1')
        h,a=map(int,value.split(':'));codes.append(score_code(h,a))
    return struct.pack('>'+'H'*len(codes),*codes)


def decode_pattern(value):
    return [score_text(c[0]) for c in struct.iter_unpack('>H',value)]


def pack_outcomes(values):
    return b''.join(OUTCOME.pack(code,*counts) for code,counts in sorted(values.items()) if counts[1]>0)


def unpack_outcomes(value):
    return {code:[matches,traces,seasons] for code,matches,traces,seasons in OUTCOME.iter_unpack(value)}


def partition_counts(rows):
    """One season's counters. Deduplicate shared next-match IDs within a context.
    Unknown days and conflicting duplicate appearances terminate each window.
    """
    teams={}
    for row in rows:
        token=score_code(row['ft_home'],row['ft_away'])
        for team in (row['home'],row['away']):
            days=teams.setdefault(team,[None]*30);i=row['day']-1
            if not 0<=i<30:continue
            days[i]=False if days[i] is not None else (token,row['id'])
    output={}
    for days in teams.values():
        for start in range(30):
            key=b''
            for end in range(start,30):
                item=days[end]
                if not item:break
                key+=struct.pack('>H',item[0])
                value=output.setdefault(key,[0,{}]);value[0]+=1
                if end==29 or not days[end+1]:continue
                next_code,next_id=days[end+1]
                pair=value[1].setdefault(next_code,[set(),0]);pair[0].add(next_id);pair[1]+=1
    return {key:[occurrences,{code:[len(matches),traces] for code,(matches,traces) in outcomes.items()}]
            for key,(occurrences,outcomes) in output.items()}


def compress_counts(counts):
    value=[[key.hex(),occ,[[code,*pair] for code,pair in sorted(outcomes.items())]] for key,(occ,outcomes) in counts.items()]
    return zlib.compress(json.dumps(value,separators=(',',':')).encode(),6)


def decompress_counts(blob):
    return {bytes.fromhex(key):[occ,{code:[matches,traces] for code,matches,traces in outcomes}]
            for key,occ,outcomes in json.loads(zlib.decompress(blob))}


class SequenceIndex:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row;self.db.executescript(SCHEMA)
        self.lock=threading.RLock();self.status='ready' if self.meta('indexed_revision') is not None else 'not_built'
        self.error=None
        self.public_state=self.summary()

    def meta(self,key,default=None):
        row=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value,separators=(',',':'))))

    def _replace_partition(self,old,new):
        for key in set(old)|set(new):
            was=old.get(key,[0,{}]);now=new.get(key,[0,{}])
            row=self.db.execute('SELECT occurrences,seasons,outcomes FROM patterns WHERE context=?',(key,)).fetchone()
            total=(row['occurrences'] if row else 0)+now[0]-was[0]
            seasons=(row['seasons'] if row else 0)+int(bool(now[0]))-int(bool(was[0]))
            if total<=0:
                self.db.execute('DELETE FROM patterns WHERE context=?',(key,));continue
            outcomes=unpack_outcomes(row['outcomes']) if row else {}
            for sign,part in [(-1,was[1]),(1,now[1])]:
                for code,(matches,traces) in part.items():
                    counts=outcomes.setdefault(code,[0,0,0]);counts[0]+=sign*matches;counts[1]+=sign*traces;counts[2]+=sign
            outcomes={code:counts for code,counts in outcomes.items() if counts[1]>0}
            if any(min(counts)<0 for counts in outcomes.values()) or seasons<0:raise ValueError('Invalid index counters')
            self.db.execute('INSERT OR REPLACE INTO patterns VALUES(?,?,?,?,?)',(key,len(key)//2,total,seasons,pack_outcomes(outcomes)))

    def sync(self,store):
        with store.lock:
            revision=store.fp_revision;archive=store.get_meta('archive_id','legacy')
            with self.lock:
                if self.meta('indexed_revision')==revision and self.meta('archive_id')==archive:
                    return self.summary()
            rows=store.all("SELECT id,season,day,home,away,ft_home,ft_away FROM matches WHERE status='final' AND ft_home IS NOT NULL AND ft_away IS NOT NULL ORDER BY season,day,id")
        self.status='indexing';self.error=None;self.public_state={**self.public_state,'status':'indexing'};began=time.perf_counter()
        groups=defaultdict(list);vocabulary=Counter()
        for row in rows:groups[row['season']].append(row);vocabulary[score_code(row['ft_home'],row['ft_away'])]+=1
        with self.lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                if self.meta('archive_id') not in (None,archive):
                    self.db.execute('DELETE FROM patterns');self.db.execute('DELETE FROM partitions')
                old_parts={r['season']:dict(r) for r in self.db.execute('SELECT * FROM partitions')}
                changed=0
                for season in sorted(set(groups)|set(old_parts)):
                    source_rows=groups.get(season,[])
                    fingerprint=hashlib.sha256(json.dumps(source_rows,separators=(',',':')).encode()).hexdigest()
                    old=old_parts.get(season)
                    if old and source_rows and old['fingerprint']==fingerprint:continue
                    before=decompress_counts(old['counts']) if old else {}
                    after=partition_counts(source_rows) if source_rows else {}
                    self._replace_partition(before,after)
                    if source_rows:self.db.execute('INSERT OR REPLACE INTO partitions VALUES(?,?,?)',(season,fingerprint,compress_counts(after)))
                    else:self.db.execute('DELETE FROM partitions WHERE season=?',(season,))
                    changed+=1
                self.db.execute('DELETE FROM scores')
                self.db.executemany('INSERT INTO scores VALUES(?,?,?,?,?)',[(code,score_text(code),(code-1)//100,(code-1)%100,count) for code,count in vocabulary.items()])
                self.set_meta('archive_id',archive);self.set_meta('indexed_revision',revision)
                self.set_meta('updated_at',time.time());self.set_meta('source_final_matches',len(rows));self.set_meta('version',INDEX_VERSION)
                self.set_meta('last_build_ms',round((time.perf_counter()-began)*1000,2));self.set_meta('changed_seasons',changed)
                self.db.execute('COMMIT');self.status='ready'
            except Exception as error:
                self.db.execute('ROLLBACK');self.status='error';self.error=str(error);self.public_state={**self.public_state,'status':'error','error':str(error)};raise
            self.public_state=self.summary()
            return self.public_state

    def summary(self):
        with self.lock:
            rows=self.db.execute('SELECT COUNT(*) patterns,COALESCE(SUM(occurrences),0) occurrences FROM patterns').fetchone()
            return {'status':self.status,'error':self.error,'index_version':INDEX_VERSION,'file':'data/ft-sequences.sqlite',
                    'unique_scores':self.db.execute('SELECT COUNT(*) FROM scores').fetchone()[0],
                    'observed_patterns':rows['patterns'],'team_sequence_occurrences':rows['occurrences'],
                    'seasons':self.db.execute('SELECT COUNT(*) FROM partitions').fetchone()[0],
                    'source_final_matches':self.meta('source_final_matches',0),'indexed_revision':self.meta('indexed_revision'),
                    'updated_at':self.meta('updated_at'),'last_build_ms':self.meta('last_build_ms'),
                    'order_counts':[dict(r) for r in self.db.execute('SELECT order_len,COUNT(*) patterns FROM patterns GROUP BY order_len ORDER BY order_len')],
                    'database_bytes':self.db.execute('PRAGMA page_count').fetchone()[0]*self.db.execute('PRAGMA page_size').fetchone()[0],
                    'note':'Only observed contexts are indexed. Next-outcome shares are historical counts, not forecasts.'}

    def vocabulary(self):
        with self.lock:return [dict(r) for r in self.db.execute('SELECT score,home,away,matches FROM scores ORDER BY home,away')]

    def query(self,pattern):
        key=encode_pattern(pattern)
        with self.lock:
            row=self.db.execute('SELECT * FROM patterns WHERE context=?',(key,)).fetchone()
            values=unpack_outcomes(row['outcomes']) if row else {}
            denominator=sum(v[0] for v in values.values());traces=sum(v[1] for v in values.values())
            result=[{'score':score_text(code),'matches':count,'traces':observations,'seasons':seasons,
                     'historical_share':((count*20000+denominator)//(2*denominator))/100 if denominator else 0}
                    for code,(count,observations,seasons) in sorted(values.items(),key=lambda p:(-p[1][0],p[0]))]
            return {'pattern':decode_pattern(key),'length':len(pattern),'occurrences':row['occurrences'] if row else 0,
                    'context_seasons':row['seasons'] if row else 0,'following_matches':denominator,'following_traces':traces,
                    'without_recorded_followup':(row['occurrences'] if row else 0)-traces,'outcomes':result,
                    'updated_at':self.meta('updated_at'),'interpretation':'Recorded following FT outcomes, not upcoming-fixture predictions.'}

    def catalogue(self,length=2,offset=0,limit=25):
        if not 1<=length<=30:raise ValueError('Pattern length must be between 1 and 30')
        with self.lock:
            total=self.db.execute('SELECT COUNT(*) FROM patterns WHERE order_len=?',(length,)).fetchone()[0]
            rows=self.db.execute('SELECT * FROM patterns WHERE order_len=? ORDER BY occurrences DESC,context LIMIT ? OFFSET ?',(length,limit,offset)).fetchall()
            return {'length':length,'total':total,'offset':offset,'rows':[{'pattern':decode_pattern(r['context']),'occurrences':r['occurrences'],
                     'following_matches':sum(v[0] for v in unpack_outcomes(r['outcomes']).values()),'seasons':r['seasons']} for r in rows]}

    def backup(self,path):
        with self.lock:
            dest=sqlite3.connect(path);self.db.backup(dest);dest.close()

    def checkpoint(self,truncate=True):
        """Fold the index's write-ahead log back into the file (a long sync leaves it large)."""
        mode='TRUNCATE' if truncate else 'PASSIVE'
        with self.lock:
            try:return self.db.execute(f'PRAGMA wal_checkpoint({mode})').fetchone()
            except sqlite3.Error:return None

    def close(self):
        self.checkpoint();self.db.close()
