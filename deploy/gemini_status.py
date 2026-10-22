#!/usr/bin/env python3
"""Read-only local status; no credentials, prompts or raw model responses."""
import json
import urllib.error
import urllib.request


def main():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/dashboard?target_format=2',timeout=5) as response:
            state=json.load(response)['gemini']
        safe={key:state.get(key) for key in ['configured','status','message','model','active_model',
              'fallback_model','diagnostic','attempts_today','daily_limit','retry_at']}
        brief=state.get('briefing')
        safe['briefing'] = {key:brief.get(key) for key in ['generated_at','model','source_count','warnings']} if brief else None
        print(json.dumps(safe,ensure_ascii=False,indent=2))
    except (urllib.error.URLError,TimeoutError,ValueError,KeyError):
        raise SystemExit('앱 상태를 읽지 못했습니다. sudo systemctl status memory-brief --no-pager 로 확인하세요.') from None


if __name__=='__main__':main()
