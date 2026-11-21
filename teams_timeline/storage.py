import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

TAIPEI = timezone(timedelta(hours=8))

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def timeline_range(start, end):
    def parse(value, is_end):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            dt = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=TAIPEI)
            return dt + timedelta(days=1 if is_end else 0)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('請使用 YYYY-MM-DD 或含時區的 RFC3339')
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    a, b = parse(start, False), parse(end, True)
    if a >= b: raise ValueError('from 必須早於 to')
    return {'start': int(a.timestamp()*1000), 'end': int(b.timestamp()*1000), 'startISO': iso(a), 'endISO': iso(b), 'timeZone':'Asia/Taipei','semantics':'[start,end)'}

def parse_date(raw):
    if not raw: return None
    s = raw.strip().replace('\u200e','').replace('\u200f','')
    try:
        if re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d', s):
            dt = datetime.fromisoformat(s.replace('Z','+00:00'))
            return iso(dt.replace(tzinfo=TAIPEI) if dt.tzinfo is None else dt)
        m = re.search(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*(凌晨|清晨|上午|中午|下午|晚上)?\s*(\d{1,2}):(\d{2})',s)
        if m:
            y, month, day, period, h, minute = m.groups(); h = int(h)
            if period in ('下午','晚上','中午') and h < 12: h += 12
            if period in ('凌晨','清晨','上午') and h == 12: h = 0
            return iso(datetime(int(y),int(month),int(day),h,int(minute),tzinfo=TAIPEI))
        if re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',s,re.I):
            cleaned=re.sub(r'\s+at\s+', ' ', s, flags=re.I)
            for fmt in ('%B %d, %Y %I:%M %p','%B %d %Y %I:%M %p','%B %d, %Y %H:%M'):
                try: return iso(datetime.strptime(cleaned, fmt).replace(tzinfo=TAIPEI))
                except ValueError: pass
            dt=parsedate_to_datetime(cleaned)
            return iso(dt if dt.tzinfo else dt.replace(tzinfo=TAIPEI))
    except (ValueError, TypeError, OverflowError): pass
    return None

def compact(value): return json.dumps(value, ensure_ascii=False, separators=(',',':'))
def digest(value): return hashlib.sha256(value.encode()).hexdigest()

class Store:
    def __init__(self, path):
        self.db=sqlite3.connect(str(path)); self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS messages (key TEXT PRIMARY KEY, chat TEXT NOT NULL, native_id TEXT, author TEXT, body TEXT, timestamp TEXT, raw_time TEXT, url TEXT, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, chat TEXT, started TEXT, finished TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS run_messages (run_id TEXT, message_key TEXT, PRIMARY KEY(run_id,message_key));''')
    def start(self, chat):
        rid=str(uuid.uuid4()); self.db.execute('INSERT INTO runs(id,chat,started,status) VALUES(?,?,?,?)',(rid,chat,iso(datetime.now(timezone.utc)),'running')); self.db.commit(); return rid
    def save(self, run, chat, messages):
        with self.db:
            for m in messages:
                key=digest(compact([chat,m.get('id') or [m.get('author'),m.get('rawTime'),m.get('body')]])); now=iso(datetime.now(timezone.utc))
                self.db.execute('''INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET author=excluded.author,body=excluded.body,timestamp=COALESCE(excluded.timestamp,messages.timestamp),raw_time=excluded.raw_time,url=excluded.url,last_seen=excluded.last_seen''',(key,chat,m.get('id'),m.get('author') or '',m['body'],m.get('timestamp'),m.get('rawTime') or '',m.get('url') or '',now,now))
                self.db.execute('INSERT OR IGNORE INTO run_messages VALUES(?,?)',(run,key))
    def finish(self, run, status, detail):
        with self.db: self.db.execute('UPDATE runs SET finished=?,status=?,detail=? WHERE id=?',(iso(datetime.now(timezone.utc)),status,detail,run))
    def query(self, runs, r, offset=0, limit=100, include_undated=True):
        if not runs: return {'messages':[],'total':0,'undated':0,'nextOffset':None}
        where='key IN (SELECT message_key FROM run_messages WHERE run_id IN (SELECT value FROM json_each(?))) AND ((timestamp>=? AND timestamp<?) OR (? AND timestamp IS NULL))'
        args=(json.dumps(runs),r['startISO'],r['endISO'],int(include_undated))
        count=self.db.execute('SELECT count(*) total, coalesce(sum(timestamp IS NULL),0) undated FROM messages WHERE '+where,args).fetchone()
        rows=[dict(x) for x in self.db.execute('SELECT * FROM messages WHERE '+where+" ORDER BY coalesce(timestamp,''),chat,key LIMIT ? OFFSET ?",(*args,limit,offset))]
        return {'messages':rows,'total':count['total'],'undated':count['undated'],'nextOffset':offset+len(rows) if offset+len(rows)<count['total'] else None}
    def close(self): self.db.close()
