import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import reports


def article(title, company='samsung'):
    return dict(id=hashlib.sha256((company+title).encode()).hexdigest(), company=company,
                title=title, url='https://news.google.com/rss/articles/test', source='Test publisher',
                published=app.now(), collected=app.now(), region='국내', topic='증권가 리포트', report=1)


def sample_pdf():
    stream=b'BT /F1 12 Tf 40 780 Td (Samsung Electronics) Tj 0 -18 Td (KB Securities) Tj 0 -18 Td (Target price: KRW 100,000 -> KRW 120,000) Tj 0 -18 Td (BUY) Tj ET'
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
             b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
             b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
             b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(data)
    data+=b'xref\n0 6\n0000000000 65535 f \n'
    for position in offsets[1:]:data+=f'{position:010d} 00000 n \n'.encode()
    return data+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF\n'


class ExtractionTests(unittest.TestCase):
    def test_buyback_and_generic_buy_articles_are_not_reports(self):
        self.assertFalse(reports.is_candidate(article('55조 자사주 매수 끝났다…삼성전자 약세')))
        self.assertFalse(reports.is_candidate(article('Samsung Electronics: is it a better buy?')))
        self.assertTrue(reports.is_candidate(article('삼성전자, 목표주가 검토')))

    def test_explicit_korean_units_and_pairs(self):
        cases=[
            ('NH투자증권 삼성전자 목표주가 10만원에서 12만 5천원으로 상향',(100000,125000)),
            ('NH투자증권 삼성전자 목표주가 100,000원 → 125,000원',(100000,125000)),
            ('NH투자증권 삼성전자 목표주가 12.5만원 (종전 10만원)',(100000,125000)),
            ('NH투자증권 삼성전자 목표주가 12만5000원 제시',(None,125000)),
            ('NH투자증권 삼성전자 목표주가 (원) 100,000 → 125,000',(100000,125000)),
            ('NH투자증권 삼성전자 목표주가 (원) 125,000',(None,125000)),
            ('NH투자증권 삼성전자 목표주가 (원) 12만 5천원',(None,125000)),
            ('NH투자증권 삼성전자 목표주가 10만원에서 미정으로 변경',(100000,None)),
            ('NH투자증권 삼성전자 목표주가 (원) 100,000 → 미정',(100000,None)),
            ('KB Securities raised Samsung Electronics price target from KRW 100,000 to KRW 125,000',(100000,125000)),
        ]
        for title,expected in cases:
            with self.subTest(title=title):
                row=reports.extract_record(article(title))
                self.assertEqual((row['previous'],row['target']),expected)
                self.assertIsNotNone(row['broker'])

    def test_current_price_earnings_delta_and_foreign_currency_not_guessed(self):
        cases=[
            '삼성전자 목표주가 미제시, 현재주가 7만원',
            '삼성전자 목표주가 대신 영업이익 10조원 전망',
            '삼성전자 목표주가 평균 12만원',
            '삼성전자 목표주가를 1만원 상향',
            '삼성전자 목표주가 +1만원',
            'Samsung Electronics price target raised by KRW 10,000',
            'Samsung Electronics price target $120',
            '삼성전자 목표주가 120,000',
            '삼성전자 목표주가 10,00원',
            '삼성전자 목표주가 20만원에서 10만원으로 상향',
            '삼성전자 목표주가 (원) 200,000 → 100,000으로 상향',
            '삼성전자 목표주가 (원) 100,000 USD',
        ]
        for title in cases:
            with self.subTest(title=title):
                row=reports.extract_record(article(title))
                self.assertIsNone(row['target'])
                self.assertEqual(row['status'],'review')

    def test_missing_previous_and_reason_stay_null(self):
        row=reports.extract_record(article('KB증권 삼성전자 현재가 7만원, 목표주가 10만원 유지'))
        self.assertEqual(row['target'],100000)
        self.assertIsNone(row['previous'])
        self.assertIsNone(row['reason'])
        self.assertIn('이전 목표가',row['missing_fields'])
        self.assertEqual(row['status'],'review')

    def test_buyback_is_not_an_investment_rating_and_other_company_reason_is_not_copied(self):
        row=reports.extract_record(article('KB증권 삼성전자 목표주가 10만원, 자사주 매수'))
        self.assertIsNone(row['opinion'])
        row=reports.extract_record(article('삼성전자 · 리포트'),{'trusted_report':True,'broker':'KB증권'},
                                   body='SK하이닉스 목표주가 상향은 수요 개선을 반영')
        self.assertIsNone(row['reason'])

    def test_ambiguous_companies_brokers_and_conflicting_claims_require_review(self):
        cases=[
            '삼성전자·SK하이닉스 목표가 10만원과 30만원',
            'KB증권·NH투자증권 삼성전자 목표주가 10만원에서 12만원으로 상향',
            '삼성전자 목표주가 10만원 제시. 삼성전자 목표주가 12만원 제시.',
        ]
        for title in cases:
            with self.subTest(title=title):
                row=reports.extract_record(article(title))
                self.assertIsNone(row['target']);self.assertIsNone(row['previous'])
                self.assertEqual(row['status'],'review')

    def test_complete_statement_retains_literal_evidence_and_reason(self):
        title='NH투자증권 삼성전자 목표주가 10만원에서 12만원으로 상향, 투자의견 매수 유지, HBM 수익성 개선을 반영'
        row=reports.extract_record(article(title))
        self.assertEqual(row['status'],'extracted')
        self.assertEqual(row['opinion'],'매수')
        self.assertEqual(row['reason'],title)
        self.assertEqual(row['evidence'],title)
        self.assertEqual(row['date_kind'],'article')

    def test_old_and_new_quotes_from_different_sentences_are_not_joined(self):
        row=reports.extract_record(article('삼성전자 기존 목표주가 10만원. 삼성전자 목표주가 12만원.'))
        self.assertIsNone(row['previous'])
        self.assertEqual(row['target'],120000)

    def test_google_related_story_descriptions_are_not_used(self):
        self.assertEqual(reports.rss_summary('<ol><li><a href="a">삼성전자 목표가 10만원</a></li><li><a href="b">다른 기사 목표가 20만원</a></li></ol>'),'')
        self.assertIn('목표가 10만원', reports.rss_summary('<p>삼성전자 목표가 10만원</p>'))

    def test_research_information_binds_company_broker_date_and_public_pdf(self):
        payload=dict(items=[dict(nid='123',itemCode='005930',itemName='삼성전자',title='HBM 수요 개선',
                                brokerName='한국투자증권',writeDate='2026-10-22',goalPrice='120000',prevGoalPrice='100000',
                                priceAtWriteDate='70000',opinionText='매수',attachUrl='https://stock.pstatic.net/research/test.pdf')])
        rows=reports.parse_research_items(payload,'samsung')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['detail']['report_date'],'2026-10-22')
        self.assertEqual(rows[0]['detail']['broker'],'한국투자증권')
        self.assertEqual(reports.parse_research_items(payload,'sk'),[])
        row=reports.extract_record(article(rows[0]['title']),rows[0]['detail'])
        self.assertEqual(row['broker'],'한국투자증권')
        self.assertEqual((row['previous'],row['target']),(100000,120000))
        self.assertEqual(row['opinion'],'매수')
        self.assertEqual(row['date_kind'],'report')
        self.assertIn('goalPrice',row['evidence'])

    def test_public_metadata_does_not_use_current_price_or_zero_as_a_target(self):
        payload=dict(nid='123',itemCode='005930',itemName='삼성전자',title='수요 개선',brokerName='KB증권',
                     writeDate='2026-10-22',goalPrice='0',prevGoalPrice=None,priceAtWriteDate='70000')
        source=reports.parse_research_items(payload,'samsung')[0]
        row=reports.extract_record(article(source['title']),source['detail'])
        self.assertIsNone(row['target']);self.assertIsNone(row['previous'])

    def test_metadata_and_body_disagreement_is_flagged_and_not_arbitrarily_resolved(self):
        payload=dict(nid='123',itemCode='005930',itemName='삼성전자',title='목표주가 검토',brokerName='KB증권',
                     writeDate='2026-10-22',goalPrice='120000',prevGoalPrice='100000')
        source=reports.parse_research_items(payload,'samsung')[0]
        row=reports.extract_record(article(source['title']),source['detail'],body='삼성전자 목표주가 15만원 제시')
        self.assertIsNone(row['target'])
        self.assertEqual(row['previous'],100000)
        self.assertIn('변경 목표가 본문·공개정보 불일치',row['missing_fields'])

    def test_other_company_in_pdf_does_not_replace_company_bound_public_fields(self):
        payload=dict(nid='123',itemCode='005930',itemName='삼성전자',title='수요 개선',brokerName='KB증권',
                     writeDate='2026-10-22',goalPrice='120000',prevGoalPrice='100000')
        source=reports.parse_research_items(payload,'samsung')[0]
        row=reports.extract_record(article(source['title']),source['detail'],body='SK하이닉스 목표주가 30만원 제시')
        self.assertEqual((row['previous'],row['target']),(100000,120000))

    def test_public_body_reader_ignores_recommendations_and_scripts(self):
        payload=b'''<html><script>target price KRW 999999</script><nav>target price KRW 888888</nav>
        <script type="application/ld+json">{"@type":"NewsArticle","articleBody":"Samsung Electronics price target KRW 120000"}</script></html>'''
        self.assertEqual(reports.article_body(payload),'Samsung Electronics price target KRW 120000')

    @unittest.skipUnless(shutil.which('pdftotext'),'poppler-utils required for the real PDF integration test')
    def test_real_public_pdf_text_conversion(self):
        text=reports.pdf_text(sample_pdf())
        self.assertIn('Samsung Electronics',text)
        row=reports.extract_record(article('삼성전자 · 공개 리포트'),{'trusted_report':True,'broker':'KB증권','report_date':'2026-10-22'},text,'public_report')
        self.assertEqual((row['previous'],row['target']),(100000,120000))
        self.assertEqual(row['opinion'],'매수')
        self.assertEqual(row['source_kind'],'public_report')

    def test_invalid_pdf_and_unapproved_destinations_rejected(self):
        with self.assertRaises(ValueError):reports.pdf_text(b'<html>not a PDF</html>')
        for url in ['http://stock.pstatic.net/a.pdf','https://127.0.0.1/test','https://169.254.169.254/metadata','https://evil.example/a.pdf','https://user:pass@www.hankyung.com/a']:
            with self.subTest(url=url):
                self.assertFalse(reports.allowed_url(url))
                with self.assertRaises(ValueError):reports.fetch_public(url)
        with self.assertRaises(ValueError):
            reports.PublicRedirects().redirect_request(None,None,302,'',{},'https://127.0.0.1/')


class AutoPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=app.DB
        app.DB=Path(self.temp.name)/'test.db';app.init_db()
        public=patch('reports.public_sources',return_value=([],[]));public.start();self.addCleanup(public.stop)

    def tearDown(self):
        app.DB=self.old;self.temp.cleanup()

    def test_existing_manual_data_schema_and_numbers_preserved(self):
        record=dict(company='samsung',broker='직접 확인 증권사',previous=70000,target=80000,opinion='매수',reason='Manual evidence',url='https://example.com/report',published='2020-01-01')
        rowid=app.save_target(record)
        with app.connect() as db:
            before=tuple(db.execute('SELECT * FROM targets WHERE id=?',(rowid,)).fetchone())
            schema=[tuple(r) for r in db.execute('PRAGMA table_info(targets)')]
        candidate=article('KB증권 삼성전자 목표주가 10만원 제시')
        app.store_articles([candidate]);app.init_db();app.backfill_targets();app.backfill_targets()
        with app.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM targets WHERE id=?',(rowid,)).fetchone()),before)
            self.assertEqual([tuple(r) for r in db.execute('PRAGMA table_info(targets)')],schema)
        targets=app.snapshot()['targets']
        self.assertEqual(len(targets),2)
        automatic=next(t for t in targets if t['origin']=='auto')
        self.assertIsNone(automatic['previous'])  # never borrowed from the manually stored broker/price
        self.assertEqual(automatic['target'],100000)

    def test_repeat_collection_deduplicates_and_updates_source_evidence(self):
        candidate=article('NH투자증권 삼성전자 목표주가를 검토')
        candidate['summary']='NH투자증권 삼성전자 목표주가 10만원 제시'
        app.store_articles([candidate]);app.collect_targets()
        self.assertEqual(app.snapshot()['targets'][0]['target'],100000)
        candidate['summary']='NH투자증권 삼성전자 목표주가 12만원 제시'
        app.store_articles([candidate]);app.collect_targets();app.collect_targets()
        targets=app.snapshot()['targets'];self.assertEqual(len(targets),1)
        self.assertEqual(targets[0]['target'],120000)
        self.assertIn('12만원',targets[0]['evidence'])

    def test_identical_titles_from_distinct_reports_keep_their_own_brokers_and_urls(self):
        date=app.now()[:10]
        raw=[dict(nid=nid,itemCode='005930',itemName='삼성전자',title='실적 개선',brokerName=broker,
                  writeDate=date,goalPrice=price,prevGoalPrice=None)
             for nid,broker,price in [('111','KB증권','100000'),('222','NH투자증권','120000')]]
        sources=reports.parse_research_items({'items':raw},'samsung')
        with patch('reports.public_sources',return_value=(sources,[])):
            app.collect_targets();app.collect_targets()
        targets=app.snapshot()['targets']
        self.assertEqual(len(targets),2)
        self.assertEqual({t['url'].rsplit('/',1)[1]:t['target'] for t in targets},{'111':100000,'222':120000})
        self.assertEqual({t['broker'] for t in targets},{'KB증권','NH투자증권'})

    def test_report_candidates_are_not_crowded_out_by_general_news(self):
        report=article('KB증권 삼성전자 목표주가 10만원 제시')
        report['published']=(app.datetime.now(app.KST)-app.timedelta(days=10)).isoformat()
        news=[dict(article('삼성전자 일반 소식 '+str(i)),report=0) for i in range(601)]
        app.store_articles([report]+news)
        app.backfill_targets()
        self.assertEqual(app.snapshot()['targets'][0]['target'],100000)

    def test_dismissed_auto_records_stay_hidden_and_failures_preserve_manual(self):
        candidate=article('KB증권 삼성전자 목표주가 10만원 제시')
        app.store_articles([candidate]);app.collect_targets()
        with app.connect() as db:db.execute('UPDATE auto_targets SET dismissed=1')
        app.collect_targets();self.assertEqual(app.snapshot()['targets'],[])
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM auto_targets').fetchone()[0],1)

    def test_inaccessible_pdf_keeps_metadata_but_never_fills_price(self):
        source=article('삼성전자 · HBM 성장 전망')
        detail={'trusted_report':True,'broker':'KB증권','report_date':'2026-10-22','pdf_url':'https://stock.pstatic.net/test.pdf'}
        with patch('reports.fetch_public',side_effect=OSError('denied')):
            row=reports.enrich(source,detail)
        self.assertEqual(row['broker'],'KB증권')
        self.assertIsNone(row['previous']);self.assertIsNone(row['target'])
        self.assertEqual(row['status'],'review');self.assertTrue(row['notes'])


if __name__=='__main__':unittest.main()
