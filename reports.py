"""Conservative, evidence-based extraction from public research sources.

No price estimation, currency conversion, LLM calls, or authentication bypasses.
Only allowlisted public publishers are fetched; Google RSS text is not a full report.
"""
import html
import json
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

VERSION = '1'
KST = timezone(timedelta(hours=9))
COMPANY_PATTERNS = {'samsung': r'삼성\s*전자|Samsung\s+Electronics',
                    'sk': r'SK\s*하이닉스|SK\s*Hynix'}
BROKERS = {
    '한국투자증권': r'한국투자증권|한투증권|한국투자(?=[\s\-·:,\'"“‘]|$)',
    'NH투자증권': r'NH투자증권|NH증권', 'KB증권': r'KB증권|KB\s+Securities',
    '삼성증권': r'삼성증권|Samsung\s+Securities', '미래에셋증권': r'미래에셋증권|Mirae\s+Asset',
    '신한투자증권': r'신한투자증권|신한금융투자', '하나증권': r'하나증권|하나금융투자',
    '키움증권': r'키움증권', '대신증권': r'대신증권', '메리츠증권': r'메리츠증권',
    '유안타증권': r'유안타증권', 'DB증권': r'DB증권|DB금융투자',
    'iM증권': r'iM증권|하이투자증권|아이엠증권', 'IBK투자증권': r'IBK투자증권',
    'BNK투자증권': r'BNK투자증권', '유진투자증권': r'유진투자증권',
    'LS증권': r'LS증권|이베스트투자증권', '한화투자증권': r'한화투자증권|한화증권',
    '교보증권': r'교보증권', '현대차증권': r'현대차증권', '상상인증권': r'상상인증권',
    'SK증권': r'SK증권', '한양증권': r'한양증권', '다올투자증권': r'다올투자증권',
    '케이프투자증권': r'케이프투자증권', '토스증권': r'토스증권',
    'DS투자증권': r'DS투자증권', '흥국증권': r'흥국증권', '신영증권': r'신영증권',
    'Goldman Sachs': r'Goldman\s+Sachs|골드만삭스', 'Morgan Stanley': r'Morgan\s+Stanley|모건스탠리',
    'JPMorgan': r'J\.?\s*P\.?\s*Morgan|JP모건|제이피모건', 'Citi': r'\bCiti(?:group)?\b|씨티그룹|씨티증권',
    'UBS': r'\bUBS\b', 'HSBC': r'\bHSBC\b',
    'Nomura': r'\bNomura\b|노무라', 'Macquarie': r'\bMacquarie\b|맥쿼리',
    'Jefferies': r'\bJefferies\b|제프리스', 'CLSA': r'\bCLSA\b|씨엘에스에이',
    'BofA': r'\bBofA\b|Bank\s+of\s+America|뱅크오브아메리카',
}
TARGET = r'목표\s*(?:주가|가|가격)|price\s+target|target\s+price'
NUMBER = r'\d[\d,]*(?:\.\d+)?'
MONEY = rf'(?<![\d.,+\-])(?:(?:{NUMBER}\s*[억만천]\s*)+(?:{NUMBER}\s*)?원|{NUMBER}\s*(?:원|won)|KRW\s*{NUMBER}|₩\s*{NUMBER})'
ALLOWED_HOSTS = {'www.hankyung.com', 'stock.naver.com', 'stock.pstatic.net'}
FIELD_NAMES = {'broker': '증권사', 'previous': '이전 목표가', 'target': '변경 목표가',
               'opinion': '투자의견', 'reason': '변경 사유'}


def company_hits(text):
    return {key for key, pattern in COMPANY_PATTERNS.items() if re.search(pattern, text, re.I)}


def is_candidate(article):
    text = article['title']
    relevant = re.search(TARGET + r'|투자\s*의견|\brating\b', text, re.I)
    broker_opinion = broker_names(text) and re.search(r'\b(?:buy|outperform|overweight)\b|매수[\s\'"‘’“”\-]', text, re.I) and '자사주' not in text
    return article['company'] in company_hits(text) and bool(relevant or broker_opinion)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self.hidden += 1
        if tag in {'p', 'br', 'div', 'li', 'tr'} and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value):
    parser = PlainText()
    parser.feed(value or '')
    return re.sub(r'[ \t\xa0]+', ' ', html.unescape(''.join(parser.parts))).strip()


def rss_summary(value):
    # Google descriptions may contain an entire cluster of OTHER news stories.
    # Never attach those stories' prices to this article.
    if len(re.findall(r'<a\b', value or '', re.I)) > 1 or re.search(r'<ol\b', value or '', re.I):
        return ''
    return plain_text(value)[:4000]


def broker_names(text):
    return {name for name, pattern in BROKERS.items() if re.search(pattern, text, re.I)}


def won(value):
    """Parse only an explicitly written KRW amount; never infer a missing unit."""
    for number in re.findall(NUMBER, value):
        if ',' in number and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?', number):
            return None
    value = re.sub(r'\s|,', '', value).upper().replace('KRW', '').replace('WON', '').replace('₩', '').removesuffix('원')
    try:
        total, last_unit = Decimal(0), 10**12
        position = 0
        for match in re.finditer(r'(\d+(?:\.\d+)?)([억만천]?)', value):
            if match.start() != position:
                return None
            unit = {'억': 100000000, '만': 10000, '천': 1000, '': 1}[match[2]]
            if unit >= last_unit:
                return None
            total += Decimal(match[1]) * unit
            last_unit, position = unit, match.end()
        if position != len(value) or total != total.to_integral_value() or not 0 < total <= 10_000_000:
            return None
        return int(total)
    except (InvalidOperation, ValueError):
        return None


def structured_won(value):
    # The public research UI explicitly labels goalPrice/prevGoalPrice in KRW.
    if isinstance(value, bool) or value is None:
        return None
    raw = str(value)
    if not re.fullmatch(r'\d+(?:\.0+)?', raw):
        return None
    return won(raw+'원')


def _price_claims(text):
    claims = []
    for anchor in re.finditer(TARGET, text, re.I):
        # A nearby current price or earnings label must never supply a target value.
        tail = text[anchor.end():anchor.end() + 140]
        tail = re.split(r'현재\s*주가|현재가|종가|매출|영업\s*이익|current\s+price|EPS|PER|PBR', tail, maxsplit=1, flags=re.I)[0]
        # Historical target-price lists and comparisons are deliberately left for review.
        if re.search(r'평균|최고|최저|최대|최소|격차|컨센서스|지난해|작년|과거|average|consensus', text[max(0, anchor.start()-30):anchor.end()+20], re.I):
            continue
        unit_label = re.match(r'^\s*\(\s*(?:원|KRW)\s*\)\s*[:：]?\s*', tail, re.I)
        if unit_label:
            labeled = tail[unit_label.end():]
            if re.search(r'\bUSD\b|\$|달러', labeled[:60], re.I):
                continue
            if not re.match(MONEY, labeled, re.I):
                pair = re.match(rf'({NUMBER})\s*(?:→|->|에서|to)\s*({NUMBER})(?![\d,.])', labeled, re.I)
                single = re.match(rf'({NUMBER})(?![\d,.])', labeled)
                if pair:
                    p, t = won(pair[1]+'원'), won(pair[2]+'원')
                    after = labeled[pair.end():pair.end()+18]
                    if p and t and ((re.search(r'상향|인상',after) and t<p) or (re.search(r'하향|인하',after) and t>p)):
                        continue
                    claims.append((p,t))
                elif single:
                    after = labeled[single.end():]
                    if re.match(r'\s*(?:[억만천월일년주%]|상향|하향|인상|인하|↑|↓)',after):
                        continue
                    amount = won(single[1]+'원')
                    claims.append((amount,None) if re.match(r'\s*(?:에서|→|->|to\b)',after,re.I) else (None,amount))
                continue
        matches = list(re.finditer(MONEY, tail, re.I))
        if not matches or matches[0].start() > 45:
            continue
        first = matches[0]
        if re.search(r'상향폭|하향폭|인상폭|인하폭|변동폭|차이|대비|\bby\b|increase\s+of|\bup\b|\bdown\b', tail[:first.start()], re.I):
            continue
        previous, target = None, None
        if len(matches) >= 2:
            second = matches[1]
            between = tail[first.end():second.start()]
            if re.fullmatch(r'\s*(?:에서|을|를|→|⇒|->|to)\s*', between, re.I):
                previous, target = won(first[0].upper()), won(second[0].upper())
            elif re.search(r'(?:기존|종전|이전|previous|was)', between, re.I):
                target, previous = won(first[0].upper()), won(second[0].upper())
            else:
                # More than one unbound amount might refer to different forecasts.
                continue
        else:
            if re.match(r'\s*(?:상향|하향|인상|인하|올|내렸|높였|낮췄|↑|↓)', tail[first.end():]):
                continue
            before = text[max(0, anchor.start()-12):anchor.start()] + tail[:first.start()]
            if re.search(r'기존|종전|이전|previous|old|\bfrom\b', before, re.I) or re.match(r'\s*(?:에서|→|->|to\b)', tail[first.end():], re.I):
                previous = won(first[0].upper())
            else:
                target = won(first[0].upper())
        # Refuse directionally contradictory statements rather than silently reversing prices.
        direction = text[max(0,anchor.start()-30):anchor.start()] + tail[:matches[min(1, len(matches)-1)].end()+18]
        if previous and target and ((re.search(r'상향|인상|raised|raises', direction, re.I) and target < previous)
                                   or (re.search(r'하향|인하|lowered|cuts', direction, re.I) and target > previous)):
            continue
        claims.append((previous, target))
    return claims


def extract_record(article, detail=None, body='', body_kind=None, notes=None):
    detail = detail or {}
    title = article['title']
    summary = detail.get('summary', '')
    text = '\n'.join(part for part in (title, summary, body) if part)
    confirmed_report = detail.get('trusted_report', False)
    hits = company_hits(text)
    ambiguous_company = hits != {article['company']}
    clauses = re.split(r'\n+|(?<=[.!?。])\s+', text)
    scoped = [s for s in clauses if re.search(TARGET, s, re.I) and
              (confirmed_report or company_hits(s) == {article['company']})]
    # PDF labels sometimes wrap onto the next line. Only company-bound reports permit joining.
    if confirmed_report and body:
        scoped = [title, re.sub(r'\s+', ' ', body)]
    claims = set()
    if not ambiguous_company:
        for clause in scoped:
            claims.update(_price_claims(clause))
    previous_values = {p for p, _ in claims if p is not None}
    target_values = {t for _, t in claims if t is not None}
    previous = next(iter(previous_values)) if len(previous_values) == 1 else None
    target = next(iter(target_values)) if len(target_values) == 1 else None
    conflicting = len(previous_values) > 1 or len(target_values) > 1
    if conflicting:
        previous = target = None
    elif previous and target and (previous, target) not in claims:
        # Do not join an old quote in one sentence with a new quote in another.
        previous = None
    names = broker_names(text)
    hint = detail.get('broker')
    broker = hint if confirmed_report and hint else (next(iter(names)) if len(names) == 1 else None)
    if ambiguous_company or (len(names) > 1 and not confirmed_report):
        previous = target = None
    fields = detail.get('public_fields') if confirmed_report else None
    metadata_conflicts = []
    if fields:
        for key, source_key in [('previous','prevGoalPrice'),('target','goalPrice')]:
            explicit = structured_won(fields.get(source_key))
            parsed = previous if key == 'previous' else target
            if explicit is not None:
                if parsed is not None and parsed != explicit:
                    metadata_conflicts.append(FIELD_NAMES[key]+' 본문·공개정보 불일치')
                    explicit = None
                if key == 'previous':
                    previous = explicit
                else:
                    target = explicit
    opinions = set()
    for clause in clauses:
        if not confirmed_report and company_hits(clause) != {article['company']} and not (
                hits == {article['company']} and '투자의견' in clause):
            continue
        if not confirmed_report and not re.search(r'투자\s*의견|\brating\b',clause,re.I):
            if not broker_names(clause) or re.search(r'자사주|순매수|순매도|매수세|매도세|개인|외국인',clause):
                continue
        for match in re.finditer(r'(?<![가-힣A-Za-z])(강력매수|매수|중립|매도|비중확대|비중축소|BUY|HOLD|SELL|OUTPERFORM|OVERWEIGHT|UNDERWEIGHT)(?=$|[\s\'"‘’“”()·,\-]|의견|를|을|로)', clause, re.I):
            opinions.add({'BUY':'매수','HOLD':'중립','SELL':'매도','OUTPERFORM':'시장수익률 상회',
                          'OVERWEIGHT':'비중확대','UNDERWEIGHT':'비중축소'}.get(match[1].upper(), match[1]))
    opinion = next(iter(opinions)) if len(opinions) == 1 and not ambiguous_company else None
    if fields and isinstance(fields.get('opinionText'), str) and fields['opinionText'].strip():
        opinion = plain_text(fields['opinionText'])[:80]
    reason = None
    for clause in clauses:
        if re.search(r'때문|반영|에\s*따라|근거|감안|이유|배경|덕분', clause) and re.search(TARGET+r'|투자\s*의견',clause,re.I) and (
                company_hits(clause) == {article['company']} or (confirmed_report and hits == {article['company']})):
            reason = clause.strip()[:600]
            break
    article_date = datetime.fromisoformat(article['published']).astimezone(KST).date().isoformat()
    evidence = '\n'.join(dict.fromkeys(scoped))[:1100] or title
    if fields:
        evidence = '공개 리포트 정보 (목표가 단위: 원)\n'+json.dumps(fields,ensure_ascii=False)+'\n\n본문 발췌\n'+evidence
    record = dict(id='auto-'+article['id'], article_id=article['id'], company=article['company'],
                  broker=broker, previous=previous, target=target, opinion=opinion,
                  reason=reason, url=article['url'], title=title, source=article['source'],
                  published=detail.get('report_date') or article_date,
                  date_kind='report' if detail.get('report_date') else 'article',
                  origin='auto', source_kind=body_kind or ('report_metadata' if confirmed_report else 'rss'),
                  evidence=evidence[:2200],
                  notes=list(notes or []), version=VERSION)
    missing = [label for key, label in FIELD_NAMES.items() if not record[key]]
    if ambiguous_company and not fields:
        missing.insert(0, '기업별 수치 구분')
    missing = metadata_conflicts + missing
    if conflicting:
        missing.insert(0, '서로 다른 목표가')
    if len(names) > 1 and not confirmed_report:
        missing.insert(0, '증권사별 수치 구분')
    record['missing_fields'] = missing
    record['status'] = 'review' if missing else 'extracted'
    record['updated'] = datetime.now(timezone.utc).isoformat()
    return record


def allowed_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        return parsed.scheme == 'https' and parsed.hostname in ALLOWED_HOSTS and parsed.port in (None, 443) and not parsed.username and not parsed.password
    except ValueError:
        return False


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('허용하지 않은 본문 주소로 이동했습니다.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url, limit=2_000_000):
    if not allowed_url(url):
        raise ValueError('허용하지 않은 공개 자료 주소입니다.')
    request = urllib.request.Request(url, headers={'User-Agent':'MemoryBrief/1.1 public research reader'})
    with urllib.request.build_opener(PublicRedirects()).open(request, timeout=12) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('공개 자료 크기 제한을 초과했습니다.')
        return data



def parse_research_items(value, company):
    """Npay's current public research response (no cookies or login required)."""
    if not isinstance(value, dict):
        return []
    rows = value.get('items') if 'items' in value else [value]
    if not isinstance(rows, list):
        return []
    expected_code = {'samsung':'005930','sk':'000660'}[company]
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get('itemCode') != expected_code:
            continue
        name = str(row.get('itemName') or '')
        title = str(row.get('title') or '')
        nid = str(row.get('nid') or '')
        if company_hits(name) != {company} or not title or not re.fullmatch(r'\d{1,20}',nid):
            continue
        try:
            date = datetime.fromisoformat(row.get('writeDate','')).date().isoformat()
        except (ValueError, TypeError):
            continue
        raw_broker = str(row.get('brokerName') or '').strip()[:80]
        names = broker_names(raw_broker)
        broker = next(iter(names)) if len(names)==1 else (raw_broker or None)
        pdf = row.get('attachUrl')
        if not isinstance(pdf,str) or not allowed_url(pdf) or not urllib.parse.urlsplit(pdf).path.lower().endswith('.pdf'):
            pdf = None
        # Preserve only public source fields that are actually consumed as evidence.
        public_fields = {key:row.get(key) for key in ['itemCode','itemName','brokerName','writeDate','goalPrice','prevGoalPrice','opinionText']}
        result.append(dict(company=company,title=name+' · '+title,
                           url='https://stock.naver.com/research/company/'+nid,
                           source='Npay 증권 공개 리포트',published=date+'T00:00:00+09:00',
                           detail=dict(trusted_report=True,broker=broker,pdf_url=pdf,report_date=date,
                                       public_fields=public_fields,public_text=plain_text(str(row.get('content') or ''))[:50000],
                                       nid=nid)))
    return result


def public_sources():
    """Bounded public RSS/report listing reads; failures don't erase existing data."""
    sources, errors = [], []
    try:
        data = fetch_public('https://www.hankyung.com/feed/finance')
        root = ET.fromstring(data)
        from email.utils import parsedate_to_datetime
        for item in root.findall('./channel/item')[:100]:
            title = plain_text(item.findtext('title') or '')
            companies = company_hits(title)
            for company in companies:
                candidate = {'company':company, 'title':title}
                if not is_candidate(candidate):
                    continue
                url = item.findtext('link') or ''
                if not allowed_url(url):
                    continue
                try:
                    date = parsedate_to_datetime(item.findtext('pubDate')).isoformat()
                except (TypeError, ValueError):
                    continue
                sources.append(dict(company=company, title=title, url=url, source='한국경제', published=date,
                                    detail={'summary':rss_summary(item.findtext('description') or '')}))
    except Exception:
        errors.append('한국경제 공개 RSS 접근 실패 — 기존 RSS 기사에서 추출을 계속합니다.')
    for company, code in [('samsung','005930'),('sk','000660')]:
        try:
            # Observed in the publisher's public research UI; use the current service,
            # not the retired finance.naver.com HTML list.
            base = 'https://stock.naver.com/api/stockSecurity/researches/v2/company'
            url = base+'?'+urllib.parse.urlencode(dict(itemCodes=code,index=0,size=3))
            rows = parse_research_items(json.loads(fetch_public(url)), company)
            cutoff = (datetime.now(KST)-timedelta(days=14)).date().isoformat()
            today = datetime.now(KST).date().isoformat()
            recent = [row for row in rows if cutoff <= row['detail']['report_date'] <= today]
            for row in recent[:3]:
                try:
                    details = parse_research_items(json.loads(fetch_public(base+'/'+row['detail']['nid'])),company)
                    if details and details[0]['url']==row['url'] and cutoff <= details[0]['detail']['report_date'] <= today:
                        row = details[0]
                except Exception:
                    row['detail']['fetch_notes'] = ['상세 공개 정보 접근 실패로 목록에 명시된 정보만 사용했습니다.']
                sources.append(row)
            if not rows:
                errors.append(('삼성전자' if company=='samsung' else 'SK하이닉스')+' 공개 리포트 목록을 읽지 못했습니다.')
        except Exception:
            errors.append(('삼성전자' if company=='samsung' else 'SK하이닉스')+' 공개 리포트 목록 접근 실패 — 자동 추출 범위를 제한합니다.')
    return sources, errors


class ArticleBody(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.script = [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type', '').lower() == 'application/ld+json':
            self.script = []

    def handle_data(self, text):
        if self.script is not None:
            self.script.append(text)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.blocks.append(''.join(self.script))
            self.script = None


def article_body(payload):
    parser = ArticleBody()
    parser.feed(payload.decode('utf-8', errors='replace'))
    def find(value):
        if isinstance(value, dict):
            kind = value.get('@type', [])
            kinds = [kind] if isinstance(kind, str) else kind
            if any(k in {'Article','NewsArticle','ReportageNewsArticle'} for k in kinds):
                body = value.get('articleBody')
                if isinstance(body, str):
                    return plain_text(body)
            for child in value.values():
                found = find(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find(child)
                if found:
                    return found
        return ''
    for block in parser.blocks:
        try:
            text = find(json.loads(block))
            if text:
                return text[:50000]
        except (ValueError, TypeError, RecursionError):
            continue
    return ''


def pdf_text(payload):
    tool = shutil.which('pdftotext')
    if not tool:
        raise RuntimeError('PDF 본문 도구가 설치되지 않았습니다.')
    if not payload.startswith(b'%PDF-'):
        raise ValueError('공개 PDF 형식이 아닙니다.')
    with tempfile.TemporaryDirectory(prefix='memory-report-pdf-') as tmp:
        path = Path(tmp)/'report.pdf'
        path.write_bytes(payload)
        output = Path(tmp)/'report.txt'
        command = [tool,'-f','1','-l','1','-layout','-enc','UTF-8',str(path),str(output)]
        limiter = shutil.which('prlimit')
        if limiter:
            command = [limiter,'--as=268435456','--cpu=10','--fsize=2097152','--'] + command
        subprocess.run(command,
                                capture_output=True, timeout=15, check=True)
        with output.open(encoding='utf-8') as text:
            return text.read(50000)


def enrich(article, detail=None):
    detail = detail or {}
    body, kind, notes = detail.get('public_text',''), None, list(detail.get('fetch_notes',[]))
    if detail.get('trusted_report'):
        if detail.get('pdf_url'):
            try:
                pdf = pdf_text(fetch_public(detail['pdf_url'], limit=8_000_000))
                if not pdf.strip():
                    raise ValueError('텍스트를 읽을 수 없는 PDF입니다.')
                body = body+'\n'+pdf
                kind = 'public_report'
            except Exception:
                notes.append('PDF 본문을 읽지 못해 공개 정보·문구만 사용했습니다. 확인되지 않은 수치는 비워 두었습니다.')
        else:
            notes.append('공개 PDF 링크가 없어 리포트 목록 정보만 사용했습니다.')
    elif urllib.parse.urlsplit(article['url']).hostname == 'www.hankyung.com':
        try:
            body = article_body(fetch_public(article['url']))
            if body:
                kind = 'public_article'
            else:
                notes.append('구조화된 공개 본문을 확인하지 못해 RSS 문구만 사용했습니다.')
        except Exception:
            notes.append('공개 본문 접근 실패로 RSS 문구만 사용했습니다.')
    return extract_record(article, detail, body, kind, notes)
