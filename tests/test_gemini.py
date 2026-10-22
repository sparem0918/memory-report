import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
import urllib.error
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import app
import gemini

CURRENT = datetime(2026,10,22,12,tzinfo=timezone.utc)
KEY = 'fake-test-key-not-a-real-secret'


def source(identifier='S1',company='samsung',title='삼성전자 HBM 공급 확대 검토'):
    return dict(id=identifier,companies=[company],title=title,source='공개 테스트 출처',
                published=CURRENT.isoformat(),url='https://example.com/'+identifier,basis='title',excerpt='')


def output(sources):
    def item(s):
        return dict(title='메모리 공급 계획 점검',summary='공개 자료에 따르면 메모리 공급 계획을 검토하고 있습니다.',
                    why_it_matters='실제 공급 확대 여부는 확인이 필요합니다.',watch_next='공식 발표 내용을 확인할 필요가 있습니다.',
                    source_ids=[s['id']])
    companies=[]
    for company in ['samsung','sk']:
        candidate=next((s for s in sources if company in s['companies']),None)
        if candidate:companies.append(dict(item(candidate),company=company))
    return dict(headline='메모리 공급 계획과 공식 발표를 함께 확인하세요.',highlights=[item(sources[0])],companies=companies)


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old_db=app.DB
        app.DB=Path(self.temp.name)/'test.db';app.init_db()
        environment=patch.dict(os.environ,{'GEMINI_ENABLED':'1','GEMINI_API_KEY':KEY,'GEMINI_MODEL':gemini.DEFAULT_MODEL})
        environment.start();self.addCleanup(environment.stop)

    def tearDown(self):
        app.DB=self.old_db;self.temp.cleanup()

    def test_disabled_and_unconfigured_do_not_make_requests_or_expose_secrets(self):
        for values in [{'GEMINI_ENABLED':'0'},{'GEMINI_API_KEY':''}]:
            with patch.dict(os.environ,values),patch('gemini.api_request') as request:
                self.assertEqual(gemini.refresh(app.connect)['status'],'not_configured')
                state=app.snapshot()['gemini']
                self.assertFalse(state['configured']);request.assert_not_called()
                self.assertNotIn(KEY,json.dumps(state))

    def test_source_selection_deduplicates_balances_and_omits_private_manual_records(self):
        articles=[]
        for company in ['samsung','sk']:
            for i in range(18):
                articles.append(dict(id=company+str(i),company=company,title=f'{company} HBM 계획 {i}',
                    source='공개 출처',published=CURRENT.isoformat(),url='https://example.com/news',
                    collected=CURRENT.isoformat(),region='국내',topic='HBM · AI',report=0,summary='공개 RSS 발췌'))
        articles += [dict(articles[0],id='duplicate',company='sk')]
        articles += [dict(articles[0],id='future',title='미래 기사',published=(CURRENT+timedelta(days=1)).isoformat()),
                     dict(articles[0],id='old',title='오래된 기사',published=(CURRENT-timedelta(days=4)).isoformat())]
        app.store_articles(articles)
        app.save_target(dict(company='sk',broker='비공개 직접 기록',previous=1,target=2,opinion='매수',
                             reason='개인적인 메모',url='https://example.com/private',published='2020-01-01'))
        rows=gemini.sources_for(app.connect,CURRENT)
        self.assertEqual(len(rows),24)
        self.assertTrue(all(s['basis']=='public_excerpt' for s in rows))
        text=json.dumps(rows,ensure_ascii=False)
        for private in ['개인적인 메모','비공개 직접 기록','미래 기사','오래된 기사']:
            self.assertNotIn(private,text)
        self.assertEqual(len({s['title'] for s in rows}),24)
        for company in ['samsung','sk']:
            self.assertLessEqual(sum(company in s['companies'] for s in rows),12)

    def test_sources_are_server_bound_and_missing_company_is_rejected(self):
        sources=[source(),source('S2','sk','SK하이닉스 HBM 공급 계획')]
        result=gemini.validate_output(output(sources),sources)
        self.assertEqual(result['highlights'][0]['sources'][0]['url'],sources[0]['url'])
        for mutation in ['fake_source','number','company','missing_company']:
            raw=output(sources)
            if mutation=='fake_source':raw['highlights'][0]['source_ids']=['S999']
            if mutation=='number':raw['highlights'][0]['summary']='목표주가를 999만원으로 올렸습니다.'
            if mutation=='company':raw['companies'][0]['source_ids']=['S2']
            if mutation=='missing_company':raw['companies']=[]
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):gemini.validate_output(raw,sources)

    def test_request_uses_header_key_fixed_host_structured_json_and_no_tools(self):
        sources=[source()]
        response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(output(sources),ensure_ascii=False)}]}}]}
        class Reply(io.BytesIO):pass
        with patch('gemini.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Reply(json.dumps(response).encode())
            result=gemini.generate(KEY,gemini.DEFAULT_MODEL,sources)
            request=opener.return_value.open.call_args.args[0]
        self.assertNotIn(KEY,request.full_url)
        self.assertTrue(request.full_url.startswith(gemini.API))
        self.assertEqual(request.get_header('X-goog-api-key'),KEY)
        body=json.loads(request.data)
        self.assertNotIn('tools',body)
        self.assertEqual(body['generationConfig']['responseMimeType'],'application/json')
        self.assertEqual(body['generationConfig']['maxOutputTokens'],4096)
        self.assertEqual(result['highlights'][0]['basis'],'title')

    def test_quota_error_is_sanitized_and_never_retried(self):
        failure=urllib.error.HTTPError(gemini.API,429,'secret '+KEY,{},io.BytesIO(KEY.encode()))
        with patch('gemini.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=failure
            with self.assertRaises(gemini.GeminiError) as caught:gemini.api_request(KEY,gemini.DEFAULT_MODEL,{})
            self.assertEqual(opener.return_value.open.call_count,1)
            self.assertNotIn(KEY,str(caught.exception))
        with self.assertRaises(gemini.GeminiError):gemini.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_model_configuration_checks_capability_without_generating(self):
        with patch('gemini.api_request',return_value={'supportedGenerationMethods':['generateContent']}) as request:
            gemini.check_model(KEY,gemini.DEFAULT_MODEL)
            request.assert_called_once_with(KEY,gemini.DEFAULT_MODEL)
        for invalid in [{},[],{'supportedGenerationMethods':None}]:
            with patch('gemini.api_request',return_value=invalid),self.assertRaises(gemini.GeminiError):
                gemini.check_model(KEY,gemini.DEFAULT_MODEL)

    def test_truncated_and_unreferenced_responses_preserve_prior_briefing(self):
        sources=[source()]
        for candidates in [[],[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'{}'}]}}]]:
            with patch('gemini.api_request',return_value={'candidates':candidates}),self.assertRaises(gemini.GeminiError):
                gemini.generate(KEY,gemini.DEFAULT_MODEL,sources)

    def test_flash_25_uses_no_thinking_and_schema_limits_source_ids(self):
        sources=[source()]
        reply={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(output(sources))}]}}]}
        for model in ['gemini-2.5-flash','gemini-2.5-flash-lite','gemini-3.5-flash-lite']:
            with patch('gemini.api_request',return_value=reply) as request:
                gemini.generate(KEY,model,sources)
                generation=request.call_args.args[2]['generationConfig']
            if model.startswith('gemini-2.5'):
                self.assertEqual(generation['thinkingConfig'],{'thinkingBudget':0})
            else:self.assertNotIn('thinkingConfig',generation)
            ids=generation['responseSchema']['properties']['highlights']['items']['properties']['source_ids']
            self.assertEqual(ids['items']['enum'],['S1'])

    def test_acronym_title_and_partial_verified_cards_survive_bad_sibling_card(self):
        sources=[source(),source('S2','sk','SK하이닉스 HBM 공급 계획')]
        raw=output(sources);raw['highlights'][0]['title']='HBM · DRAM'
        invalid=dict(raw['highlights'][0],summary='목표가가 999만원입니다.')
        raw['highlights'].append(invalid)
        raw['companies'][1]['source_ids']=['S1']
        result=gemini.validate_output(raw,sources,partial=True)
        self.assertEqual(len(result['highlights']),1)
        self.assertEqual(result['highlights'][0]['title'],'HBM · DRAM')
        missing=next(c for c in result['companies'] if c['company']=='sk')
        self.assertEqual(missing['title'],'확인 필요');self.assertEqual(missing['sources'],[])
        self.assertNotIn('999',json.dumps(result))
        self.assertIn('highlights:1:unsupported_numeric_claim',result['warnings'])

    def test_finish_reason_and_failed_checks_are_diagnosable_without_raw_response(self):
        sources=[source()]
        raw=output(sources)
        for item in raw['highlights']+raw['companies']:item['source_ids']=['NOT_A_SOURCE_'+KEY]
        replies=[({'candidates':[{'finishReason':'MAX_TOKENS'}]},'finish_MAX_TOKENS'),
                 ({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'bad '+KEY}]}}]},'invalid_json'),
                 ({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(raw)}]}}]},'no_verified_items')]
        for reply,code in replies:
            with patch('gemini.api_request',return_value=reply),self.assertRaises(gemini.GeminiError) as caught:
                gemini.generate(KEY,'gemini-2.5-flash',sources)
            self.assertEqual(caught.exception.diagnostic['code'],code)
            self.assertNotIn(KEY,str(caught.exception)+json.dumps(caught.exception.diagnostic))
            if code=='no_verified_items':self.assertIn('highlights:0:unrecognized_source',caught.exception.diagnostic['checks'])

    def test_only_structured_daily_per_model_quota_is_eligible_for_fallback(self):
        for quota_id,dimensions,eligible in [
            ('GenerateRequestsPerDayPerProjectPerModel-FreeTier',{},True),
            ('RequestsPerDay',{'model':'gemini-2.5-flash'},True),
            ('GenerateRequestsPerMinutePerProjectPerModel-FreeTier',{},False),
            ('RequestsPerDayPerProject',{},False)]:
            data={'error':{'details':[{'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[
                {'quotaId':quota_id,'quotaDimensions':dimensions}]}]}}
            error=urllib.error.HTTPError(gemini.API,429,KEY,{},io.BytesIO(json.dumps(data).encode()))
            with patch('gemini.urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaises(gemini.GeminiError) as caught:gemini.api_request(KEY,'gemini-2.5-flash',{})
            self.assertEqual(caught.exception.diagnostic['code']=='daily_model_quota',eligible)
            self.assertNotIn(KEY,str(caught.exception))

    def test_daily_quota_falls_back_once_and_routes_later_calls_to_lite_then_resets_next_day(self):
        sources=[source()];good=gemini.validate_output(output(sources),sources)
        quota=gemini.GeminiError('일일 모델 한도',{'code':'daily_model_quota'})
        with patch.dict(os.environ,{'GEMINI_MODEL':'gemini-2.5-flash'}),patch('gemini.sources_for',return_value=sources),patch('gemini.generate',side_effect=[quota,good,good,good]) as request:
            self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'ready')
            self.assertEqual([c.args[1] for c in request.call_args_list],['gemini-2.5-flash',gemini.FALLBACK_MODEL])
            self.assertEqual(gemini.refresh(app.connect,CURRENT+timedelta(minutes=1))['status'],'cached')
            sources[0]['title']='삼성전자 공급 계획 후속 소식'
            self.assertEqual(gemini.refresh(app.connect,CURRENT+timedelta(minutes=20))['status'],'ready')
            self.assertEqual(request.call_args.args[1],gemini.FALLBACK_MODEL)
            self.assertEqual(gemini.refresh(app.connect,CURRENT+timedelta(days=1))['status'],'ready')
            self.assertEqual(request.call_args.args[1],'gemini-2.5-flash')
        with app.connect() as db:
            self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-22'),3)
            self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-23'),1)

    def test_fallback_cannot_exceed_app_budget(self):
        with app.connect() as db:gemini.write_setting(db,'gemini_budget_2026-10-22',3)
        quota=gemini.GeminiError('일일 모델 한도',{'code':'daily_model_quota'})
        with patch.dict(os.environ,{'GEMINI_MODEL':'gemini-2.5-flash'}),patch('gemini.sources_for',return_value=[source()]),patch('gemini.generate',side_effect=quota) as request:
            self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'daily_limit')
            self.assertEqual(request.call_count,1)
        with app.connect() as db:self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-22'),4)

    def test_non_daily_errors_do_not_fallback_and_exhausted_lite_is_not_retried(self):
        for code in ['http_429','http_403','invalid_json','finish_MAX_TOKENS']:
            with self.subTest(code=code),patch('gemini.sources_for',return_value=[source()]),patch('gemini.generate',side_effect=gemini.GeminiError('失敗',{'code':code})) as request:
                with app.connect() as db:db.execute('DELETE FROM settings')
                self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'error')
                self.assertEqual(request.call_count,1)
        with app.connect() as db:db.execute('DELETE FROM settings')
        quota=gemini.GeminiError('일일 모델 한도',{'code':'daily_model_quota'})
        with patch.dict(os.environ,{'GEMINI_MODEL':'gemini-2.5-flash'}),patch('gemini.sources_for',return_value=[source()]),patch('gemini.generate',side_effect=quota) as request:
            self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'provider_daily_limit')
            self.assertEqual(request.call_count,2)
            self.assertEqual(gemini.refresh(app.connect,CURRENT+timedelta(minutes=20))['status'],'provider_daily_limit')
            self.assertEqual(request.call_count,2)
        with app.connect() as db:db.execute('DELETE FROM settings')
        with patch.dict(os.environ,{'GEMINI_MODEL':gemini.FALLBACK_MODEL}),patch('gemini.sources_for',return_value=[source()]),patch('gemini.generate',side_effect=quota) as request:
            gemini.refresh(app.connect,CURRENT)
            self.assertEqual(request.call_count,1)

    def test_success_is_cached_across_restarts_and_snapshot_does_not_expose_key(self):
        sources=[source()]
        with patch('gemini.sources_for',return_value=sources),patch('gemini.generate',return_value=gemini.validate_output(output(sources),sources)) as generate:
            self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'ready')
            app.init_db()
            self.assertEqual(gemini.refresh(app.connect,CURRENT+timedelta(hours=1))['status'],'cached')
            self.assertEqual(generate.call_count,1)
        state=gemini.snapshot(app.connect)
        self.assertEqual(state['status'],'ready');self.assertIsNotNone(state['briefing'])
        self.assertNotIn('fingerprint',state['briefing']);self.assertNotIn(KEY,json.dumps(state))

    def test_concurrent_requests_reserve_only_one_generation_attempt(self):
        sources=[source()];entered=threading.Event();release=threading.Event()
        def generate(*args):
            entered.set();release.wait(3)
            return gemini.validate_output(output(sources),sources)
        with patch('gemini.sources_for',return_value=sources),patch('gemini.generate',side_effect=generate) as request,ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(gemini.refresh,app.connect,CURRENT)
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'cooldown')
            finally:release.set()
            self.assertEqual(first.result(timeout=3)['status'],'ready')
            self.assertEqual(request.call_count,1)
        with app.connect() as db:self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-22'),1)

    def test_failures_preserve_cache_cooldown_and_daily_limit_count_failed_attempts(self):
        sources=[source()]
        start=CURRENT-timedelta(hours=5)
        with patch('gemini.sources_for',return_value=sources),patch('gemini.generate',return_value=gemini.validate_output(output(sources),sources)):
            gemini.refresh(app.connect,start)
        before=gemini.snapshot(app.connect)['briefing']
        sources[0]['title']='삼성전자 새로운 공급 계획'
        with patch('gemini.sources_for',return_value=sources),patch('gemini.generate',side_effect=RuntimeError(KEY)) as generate:
            self.assertEqual(gemini.refresh(app.connect,start+timedelta(minutes=1))['status'],'cooldown')
            for hours in [1,2,3]:
                self.assertEqual(gemini.refresh(app.connect,start+timedelta(hours=hours))['status'],'error')
            self.assertEqual(gemini.refresh(app.connect,start+timedelta(hours=4))['status'],'daily_limit')
            self.assertEqual(generate.call_count,3)
        state=gemini.snapshot(app.connect)
        self.assertEqual(state['briefing'],before);self.assertNotIn(KEY,json.dumps(state))
        with app.connect() as db:self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-22'),4)

    def test_korean_date_resets_budget_and_no_sources_does_not_consume_a_call(self):
        with patch('gemini.sources_for',return_value=[]),patch('gemini.generate') as generate:
            self.assertEqual(gemini.refresh(app.connect,CURRENT)['status'],'no_sources');generate.assert_not_called()
        with app.connect() as db:self.assertIsNone(gemini.read_setting(db,'gemini_budget_2026-10-22'))
        sources=[source()]
        with patch('gemini.sources_for',return_value=sources),patch('gemini.generate',return_value=gemini.validate_output(output(sources),sources)):
            gemini.refresh(app.connect,CURRENT+timedelta(hours=2))
            self.assertEqual(gemini.refresh(app.connect,CURRENT+timedelta(hours=3))['status'],'ready')
        with app.connect() as db:
            self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-22'),1)
            self.assertEqual(gemini.read_setting(db,'gemini_budget_2026-10-23'),1)

    def test_secret_configuration_permissions_and_invalid_input_preserves_file(self):
        path=Path(app.ROOT)/'deploy/configure_gemini.py'
        spec=importlib.util.spec_from_file_location('configure_gemini',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        directory=Path(self.temp.name)/'config'
        module.save_config(KEY,gemini.DEFAULT_MODEL,directory)
        target=directory/'gemini.env'
        self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertEqual(directory.stat().st_mode&0o777,0o700)
        before=target.read_bytes()
        with self.assertRaises(ValueError):module.save_config(KEY+'\nBAD=1',gemini.DEFAULT_MODEL,directory)
        self.assertEqual(target.read_bytes(),before)


if __name__=='__main__':unittest.main()
