import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import app

RSS = b'''<rss><channel><item><title>HBM demand - Test News</title>
<link>https://example.com/story</link><source>Test News</source>
<pubDate>Thu, 22 Oct 2026 00:00:00 GMT</pubDate></item>
<item><title>Unsafe</title><link>javascript:alert(1)</link></item>
<item><title>Bad date</title><link>https://example.com/bad</link><pubDate>invalid</pubDate></item>
</channel></rss>'''


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_db = app.DB
        app.DB = Path(self.temp.name) / 'test.db'
        app.init_db()

    def tearDown(self):
        app.DB = self.original_db
        self.temp.cleanup()

    def target(self, **overrides):
        return dict(company='sk', broker='Test Brokerage', previous=200000, target=240000,
                    opinion='매수', reason='Test earnings revision', url='https://example.com/report',
                    published='2020-01-01', **overrides)

    def test_rss_parsing_and_classification(self):
        records = app.parse_feed(RSS, 'sk', '해외')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['title'], 'HBM demand')
        self.assertEqual(records[0]['topic'], 'HBM · AI')
        self.assertEqual(records[0]['published'], '2026-10-22T00:00:00+00:00')
        self.assertEqual(app.topic_for('삼성전자 목표주가 상향'), '증권가 리포트')
        with self.assertRaises(ValueError):
            app.parse_feed(b'<html/>', 'sk', '국내')

    @patch('app.fetch_feed')
    def test_collection_deduplicates_and_reports_failure(self, fetch):
        fetch.side_effect = lambda spec: app.parse_feed(RSS, spec[0], spec[1])
        first = app.collect()
        self.assertEqual(first['successful'], 6)
        self.assertEqual(first['added'], 2)  # one per company across overlapping feeds
        self.assertEqual(app.collect()['added'], 0)
        self.assertEqual(len(app.snapshot()['articles']), 2)
        fetch.side_effect = OSError('network blocked')
        result = app.collect()
        self.assertEqual(result['successful'], 0)
        self.assertEqual(len(result['errors']), 6)
        snapshot = app.snapshot()
        self.assertEqual(len(snapshot['articles']), 2)  # failure preserves collected articles
        self.assertIsNotNone(snapshot['last_success'])
        self.assertEqual(snapshot['last_run']['successful'], 0)

    def test_schedule_uses_korean_time_and_rolls_over(self):
        for hour, minute, next_day, next_hour in [(7,59,22,8),(8,0,22,18),(17,59,22,18),(18,0,23,8)]:
            value=app.next_run(datetime(2026,10,22,hour,minute,tzinfo=app.KST))
            self.assertEqual((value.day,value.hour,value.minute),(next_day,next_hour,0))

    def test_validated_target_persistence(self):
        app.save_target(self.target())
        row=app.snapshot()['targets'][0]
        self.assertEqual(row['target'],240000)
        for key,value in [('url','javascript:alert(1)'),('company','unknown'),('previous',0),('target',True),('published','2999-01-01'),('broker','')]:
            payload=self.target();payload[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                app.save_target(payload)

    def test_http_workflow_and_origin_protection(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def request(path, data=None, method=None, headers=None):
            req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                                       method=method,headers=headers or {})
            with urllib.request.urlopen(req) as response:
                return response.status,json.load(response)
        try:
            self.assertEqual(request('/api/health')[1],{'ok':True})
            self.assertEqual(request('/api/dashboard')[1]['articles'],[])
            status,created=request('/api/targets',self.target())
            self.assertEqual(status,201)
            self.assertEqual(len(request('/api/dashboard')[1]['targets']),1)
            self.assertEqual(request('/api/targets/'+str(created['id']),method='DELETE')[1],{'deleted':True})
            self.assertEqual(request('/api/dashboard')[1]['targets'],[])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('/api/targets',self.target(),headers={'Origin':'https://different.example'})
            self.assertEqual(caught.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    unittest.main()
