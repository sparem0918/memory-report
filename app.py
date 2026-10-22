"""Memory Brief — dependency-free semiconductor news reader. Python 3.11+."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import reports
import gemini

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('MEMORY_DB', str(ROOT / '.data' / 'memory.db')))
KST = timezone(timedelta(hours=9))
LOCK = threading.Lock()
COMPANIES = {'samsung': '삼성전자', 'sk': 'SK하이닉스'}
FEEDS = [
    ('samsung', '국내', '삼성전자 반도체 when:3d', 'ko', 'KR', 'KR:ko'),
    ('sk', '국내', 'SK하이닉스 when:3d', 'ko', 'KR', 'KR:ko'),
    ('samsung', '해외', 'Samsung semiconductor when:3d', 'en-US', 'US', 'US:en'),
    ('sk', '해외', 'SK hynix when:3d', 'en-US', 'US', 'US:en'),
    ('samsung', '국내', '삼성전자 목표주가 증권 when:7d', 'ko', 'KR', 'KR:ko'),
    ('sk', '국내', 'SK하이닉스 목표주가 증권 when:7d', 'ko', 'KR', 'KR:ko'),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
            source TEXT NOT NULL, published TEXT NOT NULL, collected TEXT NOT NULL,
            company TEXT NOT NULL, region TEXT NOT NULL, topic TEXT NOT NULL,
            report INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY, company TEXT NOT NULL, broker TEXT NOT NULL,
            previous INTEGER NOT NULL, target INTEGER NOT NULL, opinion TEXT NOT NULL,
            reason TEXT NOT NULL, url TEXT NOT NULL, published TEXT NOT NULL,
            created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, started TEXT NOT NULL, completed TEXT NOT NULL,
            added INTEGER NOT NULL, successful INTEGER NOT NULL, errors TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_inputs (
            article_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auto_targets (
            article_id TEXT PRIMARY KEY, data TEXT NOT NULL,
            dismissed INTEGER NOT NULL DEFAULT 0);
        ''')


def topic_for(title):
    text = title.lower()
    if re.search(r'목표[주 ]?가|투자의견|증권|price target|rating|brokerage', text):
        return '증권가 리포트'
    if re.search(r'hbm|고대역폭', text):
        return 'HBM · AI'
    if re.search(r'nand|낸드', text):
        return 'NAND'
    if re.search(r'dram|d램|디램|메모리|memory', text):
        return 'DRAM · 메모리'
    if re.search(r'실적|매출|이익|earnings|profit|revenue', text):
        return '실적 · 투자'
    return '반도체 동향'


def parse_feed(payload, company, region):
    root = ET.fromstring(payload)
    if root.tag != 'rss':
        raise ValueError('RSS 형식이 아닌 응답입니다.')
    result = []
    for item in root.findall('./channel/item')[:60]:
        title = (item.findtext('title') or '').strip()
        url = (item.findtext('link') or '').strip()
        source = (item.findtext('source') or 'Google News').strip()
        if not title or urllib.parse.urlsplit(url).scheme != 'https':
            continue
        suffix = ' - ' + source
        if title.endswith(suffix):
            title = title[:-len(suffix)]
        try:
            published = parsedate_to_datetime(item.findtext('pubDate')).astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
        topic = topic_for(title)
        # Same publisher/headline across overlapping feeds is stored only once per company.
        key = hashlib.sha256((company + source + re.sub(r'\W', '', title).lower()).encode()).hexdigest()
        result.append(dict(id=key, title=title, url=url, source=source, published=published,
                           collected=now(), company=company, region=region, topic=topic,
                           report=int(topic == '증권가 리포트'),
                           summary=reports.rss_summary(item.findtext('description') or '')))
    return result


def fetch_feed(spec):
    company, region, query, hl, gl, ceid = spec
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode(dict(q=query, hl=hl, gl=gl, ceid=ceid))
    request = urllib.request.Request(url, headers={'User-Agent': 'MemoryBrief/1.0 RSS reader'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('RSS 응답이 너무 큽니다.')
    return parse_feed(data, company, region)


def collect():
    if not LOCK.acquire(blocking=False):
        return {'busy': True}
    try:
        started = now()
        added, successful, errors = 0, 0, []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [(spec, pool.submit(fetch_feed, spec)) for spec in FEEDS]
            for spec, future in futures:
                try:
                    articles = future.result()
                    added += store_articles(articles)
                    successful += 1
                except Exception as exc:
                    # Do not persist proxy URLs, headers, or other potentially sensitive error details.
                    errors.append(f'{COMPANIES[spec[0]]} · {spec[1]}: {type(exc).__name__} — 수집 주소 접근 또는 RSS 응답을 확인하세요.')
        try:
            target_result = collect_targets()
        except Exception:
            target_result = {'updated': now(), 'errors': ['목표주가 자동 정리 중 오류가 발생했습니다. 기존 기록은 유지됩니다.']}
        with connect() as db:
            db.execute('INSERT INTO runs(started,completed,added,successful,errors) VALUES (?,?,?,?,?)',
                       (started, now(), added, successful, json.dumps(errors, ensure_ascii=False)))
            db.execute("INSERT OR REPLACE INTO settings VALUES ('report_collection',?)",
                       (json.dumps(target_result, ensure_ascii=False),))
        briefing_result = gemini.refresh(connect)
        return dict(added=added, successful=successful, total=len(FEEDS), errors=errors,
                    target_collection=target_result, gemini=briefing_result)
    finally:
        LOCK.release()


def store_articles(articles):
    added = 0
    with connect() as db:
        for article in articles:
            cursor = db.execute('INSERT OR IGNORE INTO articles VALUES (:id,:title,:url,:source,:published,:collected,:company,:region,:topic,:report)', article)
            added += cursor.rowcount
            if not cursor.rowcount:
                db.execute('UPDATE articles SET title=?,url=?,published=?,collected=? WHERE id=?',
                           (article['title'],article['url'],article['published'],article['collected'],article['id']))
            detail = article.get('detail') or {'summary': article.get('summary', '')}
            db.execute('INSERT OR REPLACE INTO report_inputs VALUES (?,?)',
                       (article['id'], json.dumps(detail, ensure_ascii=False)))
    return added


def save_auto_target(record):
    with connect() as db:
        # Dismissed automatic records stay dismissed on every subsequent collection.
        db.execute('''INSERT INTO auto_targets(article_id,data) VALUES (?,?)
            ON CONFLICT(article_id) DO UPDATE SET data=excluded.data''',
                   (record['article_id'], json.dumps(record, ensure_ascii=False)))


def target_candidates():
    cutoff = (datetime.now(KST)-timedelta(days=14)).date().isoformat()
    with connect() as db:
        rows = db.execute('''SELECT a.*, i.data AS detail FROM articles a
            LEFT JOIN report_inputs i ON a.id=i.article_id WHERE a.published>=?
            ORDER BY a.report DESC,a.published DESC LIMIT 600''', (cutoff,)).fetchall()
    return [(dict(row), json.loads(row['detail']) if row['detail'] else {}) for row in rows]


def backfill_targets():
    """Immediate, offline-only backfill after upgrade; never changes manual rows."""
    with connect() as db:
        existing = {row[0] for row in db.execute('SELECT article_id FROM auto_targets')}
    for article, detail in target_candidates():
        if article['id'] not in existing and (reports.is_candidate(article) or detail.get('trusted_report')):
            save_auto_target(reports.extract_record(article, detail))


def collect_targets():
    raw_sources, errors = reports.public_sources()
    articles = []
    for source in raw_sources:
        # Report titles can be identical across brokers/dates. Bind to the actual source URL.
        key = hashlib.sha256((source['company']+'\n'+source['source']+'\n'+source['url']).encode()).hexdigest()
        articles.append(dict(source, id=key, region='국내', topic='증권가 리포트', report=1, collected=now()))
    store_articles(articles)
    fresh_public_ids = {a['id'] for a in articles}
    processed, body_reads = 0, 0
    # Always process the current report listings before other RSS candidates.
    candidates = target_candidates()
    candidates.sort(key=lambda pair: not pair[1].get('trusted_report', False))
    for article, detail in candidates:
        if not (reports.is_candidate(article) or detail.get('trusted_report')):
            continue
        needs_body = detail.get('trusted_report') or urllib.parse.urlsplit(article['url']).hostname == 'www.hankyung.com'
        if needs_body and article['id'] not in fresh_public_ids:
            # Archived reports retain their evidence; bounded fresh listings are refreshed.
            with connect() as db:
                if db.execute('SELECT 1 FROM auto_targets WHERE article_id=?', (article['id'],)).fetchone():
                    continue
            save_auto_target(reports.extract_record(article, detail))
            processed += 1
            continue
        if needs_body and body_reads >= 18:
            with connect() as db:
                if db.execute('SELECT 1 FROM auto_targets WHERE article_id=?', (article['id'],)).fetchone():
                    continue
            record = reports.extract_record(article, detail, notes=['본문 확인은 다음 수집에서 재시도합니다.'])
        else:
            if needs_body:
                body_reads += 1
            record = reports.enrich(article, detail)
        save_auto_target(record)
        processed += 1
    return dict(updated=now(), processed=processed, public_items=len(articles),
                pdf_reader=bool(reports.shutil.which('pdftotext')), errors=errors)


def next_run(current=None):
    current = (current or datetime.now(KST)).astimezone(KST)
    for days in (0, 1):
        for hour in (8, 18):
            candidate = (current + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > current:
                return candidate


def scheduler():
    # Claim each slot in SQLite before running; restarts cannot repeatedly execute the same slot.
    while True:
        current = datetime.now(KST)
        hours = [h for h in (8, 18) if current.hour >= h]
        slot_day = current if hours else current - timedelta(days=1)
        slot = slot_day.strftime('%Y-%m-%d') + f'-{max(hours) if hours else 18:02d}'
        with connect() as db:
            previous = db.execute("SELECT value FROM settings WHERE key='last_slot'").fetchone()
            report_version = db.execute("SELECT value FROM settings WHERE key='report_version'").fetchone()
            brief_version = db.execute("SELECT value FROM settings WHERE key='gemini_version'").fetchone()
            needs_brief = gemini.configured() and (brief_version is None or brief_version['value'] != gemini.VERSION)
            if previous is None or previous['value'] != slot or report_version is None or report_version['value'] != reports.VERSION or needs_brief:
                if not LOCK.locked():
                    db.execute("INSERT OR REPLACE INTO settings VALUES ('last_slot',?)", (slot,))
                    db.execute("INSERT OR REPLACE INTO settings VALUES ('report_version',?)", (reports.VERSION,))
                    if gemini.configured():
                        db.execute("INSERT OR REPLACE INTO settings VALUES ('gemini_version',?)", (gemini.VERSION,))
                    should_run = True
                else:
                    should_run = False
            else:
                should_run = False
        if should_run:
            collect()
        time.sleep(20)


def snapshot():
    with connect() as db:
        articles = [dict(r) for r in db.execute('SELECT * FROM articles ORDER BY published DESC LIMIT 600')]
        manual = [dict(r, origin='manual', status='manual', missing_fields=[], date_kind='report')
                  for r in db.execute('SELECT * FROM targets ORDER BY published DESC,id DESC LIMIT 200')]
        automatic = [json.loads(r['data']) for r in db.execute('SELECT data FROM auto_targets WHERE dismissed=0')]
        automatic = sorted(automatic, key=lambda row: (row['published'],str(row['id'])), reverse=True)[:200]
        targets = sorted(manual + automatic, key=lambda row: (row['published'],str(row['id'])), reverse=True)
        report_run = db.execute("SELECT value FROM settings WHERE key='report_collection'").fetchone()
        last = db.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 1').fetchone()
        last_ok = db.execute('SELECT completed FROM runs WHERE successful>0 ORDER BY id DESC LIMIT 1').fetchone()
    run = dict(last) if last else None
    if run:
        run['errors'] = json.loads(run['errors'])
    return dict(articles=articles, targets=targets, last_run=run,
                gemini=gemini.snapshot(connect),
                target_collection=json.loads(report_run['value']) if report_run else None,
                last_success=last_ok['completed'] if last_ok else None,
                next_run=next_run().isoformat(), collecting=LOCK.locked(), schedule=['08:00', '18:00'], timezone='Asia/Seoul')


def save_target(data):
    company = data.get('company')
    if company not in COMPANIES:
        raise ValueError('기업을 선택하세요.')
    cleaned = {'company': company}
    for field, maximum in [('broker', 80), ('opinion', 40), ('reason', 1000), ('url', 2000), ('published', 10)]:
        value = str(data.get(field, '')).strip()
        if not value or len(value) > maximum:
            raise ValueError('증권사·투자의견·변경 사유·출처·발표일을 확인하세요.')
        cleaned[field] = value
    parsed = urllib.parse.urlsplit(cleaned['url'])
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('출처는 유효한 HTTPS 주소를 입력하세요.')
    try:
        date = datetime.strptime(cleaned['published'], '%Y-%m-%d').date()
        if date > datetime.now(KST).date():
            raise ValueError()
    except ValueError:
        raise ValueError('발표일은 오늘 또는 과거 날짜를 입력하세요.') from None
    for field in ('previous', 'target'):
        value = data.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= 10_000_000:
            raise ValueError('목표주가는 1원부터 1,000만원 사이의 정수로 입력하세요.')
        cleaned[field] = value
    with connect() as db:
        cursor = db.execute('''INSERT INTO targets(company,broker,previous,target,opinion,reason,url,published,created)
            VALUES (:company,:broker,:previous,:target,:opinion,:reason,:url,:published,:created)''', {**cleaned, 'created': now()})
        return cursor.lastrowid


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def respond(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/dashboard':
            result = snapshot()
            # Old, still-open browser tabs assume every price is non-null. Keep their
            # original manual-only response until they load the updated frontend.
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if query.get('target_format') != ['2']:
                result['targets'] = [row for row in result['targets'] if row['origin'] != 'auto']
            return self.respond(result)
        if path == '/api/health':
            with connect() as db:
                db.execute('SELECT 1').fetchone()
            return self.respond({'ok': True})
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in files:
            return self.respond({'error': '찾을 수 없습니다.'}, 404)
        name, mime = files[path]
        payload = (ROOT / 'static' / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def valid_origin(self):
        origin = self.headers.get('Origin')
        fetch_site = self.headers.get('Sec-Fetch-Site', '')
        return fetch_site != 'cross-site' and (not origin or urllib.parse.urlsplit(origin).netloc == self.headers.get('Host'))

    def do_POST(self):
        if not self.valid_origin():
            return self.respond({'error': '동일한 사이트에서 요청하세요.'}, 403)
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/refresh':
            if LOCK.locked():
                return self.respond({'busy': True}, 202)
            threading.Thread(target=collect, daemon=True).start()
            return self.respond({'started': True}, 202)
        if path != '/api/targets':
            return self.respond({'error': '찾을 수 없습니다.'}, 404)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                raise ValueError('요청 크기를 확인하세요.')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('입력 형식을 확인하세요.')
            return self.respond({'id': save_target(data)}, 201)
        except (ValueError, TypeError) as exc:
            return self.respond({'error': str(exc)}, 400)

    def do_DELETE(self):
        if not self.valid_origin():
            return self.respond({'error': '동일한 사이트에서 요청하세요.'}, 403)
        auto = re.fullmatch(r'/api/targets/auto-([a-f0-9]{64})', self.path)
        if auto:
            with connect() as db:
                cursor = db.execute('UPDATE auto_targets SET dismissed=1 WHERE article_id=? AND dismissed=0', (auto[1],))
            return self.respond({'deleted': cursor.rowcount == 1})
        match = re.fullmatch(r'/api/targets/(\d+)', self.path)
        if not match:
            return self.respond({'error': '찾을 수 없습니다.'}, 404)
        with connect() as db:
            cursor = db.execute('DELETE FROM targets WHERE id=?', (int(match[1]),))
        self.respond({'deleted': cursor.rowcount == 1})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--collect', action='store_true', help='Collect once and exit')
    parser.add_argument('--brief-only', action='store_true', help='Update cached Gemini summary without collecting news')
    parser.add_argument('--no-scheduler', action='store_true')
    args = parser.parse_args()
    init_db()
    backfill_targets()
    if args.brief_only:
        result = gemini.refresh(connect)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1 if result['status'] == 'error' else 0)
    if args.collect:
        result = collect()
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(0 if result.get('successful') else 1)
    if not args.no_scheduler:
        threading.Thread(target=scheduler, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'Memory Brief listening on port {args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
