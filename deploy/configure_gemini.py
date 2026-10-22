#!/usr/bin/env python3
"""Run on the dedicated VM: sudo python3 deploy/configure_gemini.py.

Never pass the key as a command argument, print it, or store it in the checkout.
"""
import getpass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import gemini

DIRECTORY = Path('/etc/memory-brief')


def save_config(key, model, directory=DIRECTORY):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,200}',key) or not gemini.valid_model(model):
        raise ValueError('API 키 또는 모델 이름의 형식을 확인하세요.')
    if directory.is_symlink():
        raise ValueError('설정 폴더가 심볼릭 링크입니다. 저장하지 않았습니다.')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.stat().st_uid != os.geteuid():
        raise ValueError('설정 폴더의 소유자를 확인하세요.')
    directory.chmod(0o700)
    target = directory/'gemini.env'
    if target.is_symlink():
        raise ValueError('설정 파일이 심볼릭 링크입니다. 저장하지 않았습니다.')
    if target.exists() and (not stat.S_ISREG(target.stat().st_mode) or target.stat().st_uid != os.geteuid()):
        raise ValueError('기존 설정 파일의 소유자 또는 형식을 확인하세요.')
    descriptor, temporary = tempfile.mkstemp(dir=directory,prefix='.gemini-')
    try:
        with os.fdopen(descriptor,'w') as stream:
            stream.write(f'GEMINI_ENABLED=1\nGEMINI_MODEL={model}\nGEMINI_API_KEY={key}\n')
            stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary,0o600)
        os.replace(temporary,target)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def main():
    if os.geteuid()!=0:
        raise SystemExit('sudo python3 deploy/configure_gemini.py 로 실행하세요.')
    if not sys.stdin.isatty():
        raise SystemExit('SSH 터미널에서 직접 실행하세요. API 키를 파이프로 전달하지 마세요.')
    if subprocess.run(['systemctl','is-active','--quiet','memory-brief']).returncode:
        raise SystemExit('먼저 sudo bash deploy/install.sh 로 앱을 설치하세요.')
    unit = subprocess.check_output(['systemctl','show','--property=FragmentPath','--value','memory-brief'],text=True).strip()
    if not unit or 'EnvironmentFile=-/etc/memory-brief/gemini.env' not in Path(unit).read_text():
        raise SystemExit('Gemini용 서비스 설정이 없습니다. 먼저 새 deploy/install.sh 를 실행하세요.')
    print('Google AI Studio의 무료 이용 대상 프로젝트에서 발급한 API 키를 사용하세요.')
    print('결제 계정을 연결하지 마세요. 이 도구는 결제·구독을 활성화하지 않습니다.')
    print('무료 이용 여부·한도는 Google 정책과 프로젝트에 따라 다릅니다. 일일 호출 제한만으로 무료가 보장되지는 않습니다.')
    print('공개 기사 제목·발췌를 Google로 전송하며 개인정보·수동 기록은 보내지 않습니다.')
    model = input(f'Gemini model [Enter: {gemini.DEFAULT_MODEL}]: ').strip() or gemini.DEFAULT_MODEL
    if not gemini.valid_model(model):
        raise SystemExit('모델 이름을 확인하세요. 설정을 변경하지 않았습니다.')
    key = getpass.getpass('Gemini API key (hidden; do not send in chat): ').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,200}',key):
        raise SystemExit('키 형식을 확인하세요. 설정을 변경하지 않았습니다.')
    try:
        gemini.check_model(key,model)  # Read-only model lookup; no generation tokens.
        save_config(key,model)
    except (gemini.GeminiError,ValueError) as error:
        raise SystemExit(str(error)+' 기존 설정은 유지됩니다.') from None
    finally:
        key = ''
    subprocess.run(['systemctl','restart','memory-brief'],check=True)
    for _ in range(15):
        try:
            request = urllib.request.Request('http://127.0.0.1:8000/api/refresh',data=b'',method='POST')
            with urllib.request.urlopen(request,timeout=3) as response:json.load(response)
            print('Gemini configured. The key was stored privately on this server.')
            print('Collecting public sources and generating a Korean briefing. Refresh the website after collection finishes.')
            print('Existing news, target prices, HTTPS and website password are preserved.')
            return
        except (urllib.error.URLError,TimeoutError):
            time.sleep(1)
    raise SystemExit('키는 저장됐지만 서버 응답 확인이 필요합니다: sudo systemctl status memory-brief --no-pager')


if __name__=='__main__':
    try:
        main()
    except (KeyboardInterrupt,EOFError):
        raise SystemExit('\n설정을 중단했습니다.') from None
