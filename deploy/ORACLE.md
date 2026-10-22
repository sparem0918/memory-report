# 오라클 서버에서 운영하기

이 파일은 **배포 준비 안내**입니다. 오라클 서버에 실제 설치하거나 공개한 상태를 뜻하지 않습니다. Ubuntu 또는 Oracle Linux 계열의 systemd 서버에서 Python 3.11 이상이 필요합니다. x86 서버와 ARM 서버 모두에 사용할 수 있으며 외부 Python 패키지를 설치하지 않습니다.

공개 리포트 PDF 읽기에는 `poppler-utils`가 필요합니다. Ubuntu 설치기는 이 무료 패키지를 자동 설치합니다. 다른 배포판에서는 해당 OS의 서명된 패키지 저장소로 먼저 설치하세요. 기본 수집에는 유료 API나 별도 구독이 필요하지 않습니다. 선택 기능인 Gemini 요약은 아래의 무료 이용 조건을 확인한 뒤 별도로 연결합니다.

## 먼저 확인할 것

오라클 서버 터미널에서 다음 명령을 실행하면 운영체제와 Python 버전을 확인할 수 있습니다. 이 명령에는 비밀번호나 개인키가 포함되지 않습니다.

```bash
cat /etc/os-release
python3 --version
uname -m
```

Python 버전이 3.11보다 낮거나 없다면, 운영체제에 맞는 패키지 설치가 먼저 필요합니다. 시스템의 기존 `python3`를 교체하거나 연결을 바꾸지 마세요. `python3.11`, `python3.12` 같은 별도 실행 파일을 사용할 수 있습니다. 다른 서비스가 실행 중이면 기존 포트·웹서버·방화벽 설정을 먼저 확인합니다.

## 파일 전달

배포 압축 파일을 내 컴퓨터에 다운로드한 뒤 SCP/SFTP로 오라클 서버에 복사합니다. SSH 접속은 기존에 사용하던 키와 사용자 계정을 사용하세요. **SSH 개인키·서버 비밀번호를 채팅에 보내지 마세요.** 다음 명령의 `KEY_PATH`, `USER`, `SERVER_IP`, `LOCAL_ARCHIVE`는 실제 환경 값으로 바꿉니다. Ubuntu의 기본 계정은 보통 `ubuntu`, Oracle Linux는 보통 `opc`이지만 실제 접속 계정을 확인하세요.

내 컴퓨터 터미널:

```bash
scp -i KEY_PATH LOCAL_ARCHIVE USER@SERVER_IP:memory-brief-oracle.tar.gz
```

서버 터미널:

```bash
mkdir -p ~/memory-brief-upload
tar -xzf ~/memory-brief-oracle.tar.gz -C ~/memory-brief-upload
cd ~/memory-brief-upload/memory-report
sudo bash deploy/install.sh
```

실행 파일을 별도로 설치한 경우에는 마지막 명령 대신 다음처럼 지정합니다.

```bash
sudo env PYTHON_BIN=python3.11 bash deploy/install.sh
```

설치기는 테스트를 먼저 실행하고, 전용 서비스 계정·릴리스 디렉터리·systemd 서비스를 만듭니다. 코드: `/opt/memory-brief/current`, 데이터: `/var/lib/memory-brief/memory.db`입니다. 기존 데이터는 유지하며 재설치 전에 SQLite backup API로 `/var/backups/memory-brief/`에 백업합니다. 처음 이전할 때는 빈 DB에서 뉴스를 새로 수집합니다. 현재 개발 환경의 DB는 배포 압축에 포함하지 않습니다.

## 처음 접속하기: SSH 터널

첫 설치는 **서버 내부의 8000번 포트에만** 연결합니다. 기존 SSH 접속 권한을 이용하므로 웹사이트 비밀번호나 HTTPS 도메인 설정 없이 개인용 화면을 확인할 수 있습니다. 오라클 인바운드 8000번 포트를 열 필요가 없습니다.

내 컴퓨터에서 다음 명령을 실행한 채 유지합니다.

```bash
ssh -i KEY_PATH -N -L 8000:127.0.0.1:8000 USER@SERVER_IP
```

컴퓨터 브라우저의 주소창에 `127.0.0.1:8000`을 입력합니다. 이는 설치를 마친 오라클 서버로 연결하는 터널이며 현재 개발 환경의 미리보기 주소가 아닙니다. 컴퓨터의 8000번 포트가 이미 사용 중이면 `-L 8800:127.0.0.1:8000`으로 바꾸고 브라우저에서는 8800번을 사용하세요.

컴퓨터나 터널을 꺼도 오라클 서버의 정기 수집은 계속됩니다. 터널은 화면을 볼 때만 필요합니다. 휴대폰에서도 일반 인터넷 주소로 접속하려면 도메인·HTTPS·로그인 또는 VPN 설정을 추가해야 합니다. 현재 앱에는 공개 사이트용 로그인 기능이 없으므로 이 단계 전에는 8000번을 인터넷에 공개하지 마세요.

## 운영 확인

```bash
sudo systemctl status memory-brief --no-pager
sudo journalctl -u memory-brief -n 50 --no-pager
```

화면에서 최근 수집 시각과 다음 수집 시각을 확인합니다. 최초 수집은 잠시 걸릴 수 있습니다. 국내외 6개 피드가 모두 수집됐는지 확인하세요. 서버에서 `news.google.com`의 HTTPS 접근과 DNS가 가능해야 합니다. 서버 시간대와 관계없이 앱의 예약은 한국 시간 오전 8시·오후 6시입니다.

정지·재시작:

```bash
sudo systemctl stop memory-brief
sudo systemctl restart memory-brief
```

## 디자인 업데이트

Gemini 업데이트를 처음 설치할 때는 아래의 `Gemini 브리핑 연결` 절차를 사용하세요. 작성자 개인정보 정리로 변경된 Git 기록을 안전하게 받아오는 단계가 포함됩니다.

GitHub에서 설치했다면 아래를 실행합니다. 설치기는 기존 DB를 백업·유지하며 새 릴리스로 전환합니다. Caddy 비밀번호와 HTTPS 설정도 그대로 유지합니다.

```bash
cd ~/memory-report
git pull --ff-only
sudo bash deploy/install.sh
```

시작 확인에 실패하면 이전 코드로 되돌립니다. 목표주가 자동 정리 업데이트는 새 테이블만 추가하며 기존 수동 기록을 변경하지 않습니다. 새 버전 시작 직후 기존 기사에서 확인 가능한 목표주가를 소급 등록하고, 새로운 공개 자료 수집을 시작합니다. 설치 출력의 `Automatic target records`와 웹 화면의 `공개 자료 자동 정리` 표시를 확인하세요. 미확인 필드는 `확인 필요`이며 금액이 0원으로 채워지지 않습니다. 백업과 이전 릴리스는 자동 삭제하지 않습니다.

서버와 저장 공간이 본인의 오라클 무료 할당 범위에 포함되는지는 오라클 콘솔에서 확인하세요. 이 설치 절차는 새 VM이나 유료 클라우드 리소스를 생성하지 않습니다.

## Gemini 브리핑 연결

`ubuntu@memory-brief` 서버 터미널에서 실행합니다. 작성자 정보 정리로 `main` 기록이 바뀌었으므로, 기존 로컬 브랜치를 보존한 채 최신 원격 버전에서 별도 배포 브랜치를 만듭니다. 기존 파일에 직접 수정한 내용이 있다면 먼저 `git status`로 확인하고 보존하세요. 브랜치 전환이 실패하면 다음 단계로 진행하지 말고 오류를 확인하세요.

```bash
cd ~/memory-report
git fetch origin
git switch -c gemini-setup --track origin/main
sudo bash deploy/install.sh
```

이미 `gemini-setup` 브랜치를 만든 뒤의 후속 업데이트라면 해당 브랜치에서 `git pull --ff-only`를 사용합니다. 기존 `main` 브랜치와 배포 DB·Caddy 설정은 보존합니다.

설치 완료 후 [Google AI Studio](https://aistudio.google.com/api-keys)에서 **결제 계정을 연결하지 않은 무료 이용 가능 프로젝트**의 API 키를 발급하고 실행합니다.

```bash
sudo python3 deploy/configure_gemini.py
```

모델 이름은 Enter로 기본값을 선택할 수 있습니다. 기본 모델이 더 이상 제공되지 않으면 AI Studio에서 확인한 무료 지원 모델 이름을 입력하세요. API 키는 서버의 숨김 입력란에만 붙여넣습니다. 설정 도구는 키와 모델을 읽기 전용 조회로 확인한 뒤 root 전용 파일에 저장하고 서버를 재시작합니다. 실제 AI 생성은 이어지는 수집에서 시도합니다. 성공하면 웹 화면에 핵심 변화·기업별 의미·다음 확인 사항이 출처와 함께 표시됩니다. 실패하면 원인 안내와 기존 브리핑을 유지합니다.

Gemini 무료 한도는 Google 정책·계정·모델에 따라 다르며, 호출 제한만으로 유료 프로젝트의 과금을 차단하지는 못합니다. 앱은 자동 결제 설정·검색 도구를 사용하지 않습니다. Google이 모델별 일일 할당량 소진을 명시하면 `gemini-2.5-flash-lite`로 한 번 대체하고, 그날 이후에는 Lite를 우선 사용합니다. 다음 한국 날짜에는 기본 모델부터 다시 시도하며 Google의 한도 초기화 시각은 다를 수 있습니다. 분당 제한이나 응답 검사 실패에는 모델을 바꾸지 않습니다. 한국 날짜 기준 실패·대체 호출을 모두 합쳐 하루 최대 4회, 수집 회차 간 최소 15분, 같은 입력 재사용을 적용합니다. 두 모델이 모두 소진되면 그날 추가 요청을 멈춥니다. 키·개인 메모·수동 목표주가 기록은 Google 요약 입력에 넣지 않습니다. 자세한 범위와 중지 방법은 README의 Gemini 설정을 참고하세요.

## 개인 웹사이트로 HTTPS 공개

전용 `memory-brief` VM의 앱 설치와 수집을 확인한 다음 진행합니다. 기존 `screener` VM이나 `screener-vcn`은 변경하지 않습니다.

1. 도메인이 있으면 새 서버의 공인 IPv4로 A 레코드를 설정합니다. 도메인을 구매하지 않을 때는 `memory-brief.PUBLIC-IP-WITH-DASHES.sslip.io` 같은 외부 무료 DNS 주소를 사용할 수 있습니다. 예를 들어 `1.2.3.4`이면 `memory-brief.1-2-3-4.sslip.io`입니다. 이 방식은 sslip.io의 가용성과 현재 공인 IP에 의존합니다. 서버에서 DNS 조회가 실제 IP로 되는지 확인해야 합니다.
2. OCI 콘솔의 **새 `memory-brief-vcn`에 연결된 공용 서브넷의 Security List**에서 상태 저장 Ingress 규칙 두 개를 추가합니다. Source CIDR은 `0.0.0.0/0`, IP Protocol은 TCP, Source Port는 비워두고 Destination Port를 각각 `80`, `443`으로 지정합니다. 기존 SSH 규칙은 유지하고 8000번은 공개하지 않습니다. NAT 게이트웨이, 로드 밸런서, 유료 DNS 영역을 만들 필요는 없습니다.
3. 새 Ubuntu 서버에서 아래를 실행합니다. `YOUR_HOSTNAME`은 실제 주소로 바꾸고 `https://`는 붙이지 않습니다.

```bash
cd ~/memory-report
git pull --ff-only
sudo bash deploy/publish.sh YOUR_HOSTNAME
```

스크립트는 Ubuntu 패키지 저장소의 Caddy를 설치하고 로그인 암호를 터미널에서 두 번 묻습니다. 암호를 입력할 때 글자가 표시되지 않는 것이 정상입니다. **암호를 채팅에 보내지 마세요.** 아이디는 `memory`입니다. 암호는 해시 형태로 서버 설정에 저장합니다. 공개 주소의 화면과 API 모두 HTTPS·비밀번호 인증으로 보호합니다.

Caddy는 무료 ACME 인증서를 자동 신청하고 갱신합니다. HTTPS 발급에는 DNS 일치, 외부에서 도달 가능한 80/443 포트, 인증기관의 발급 제한 조건이 필요합니다. 소프트웨어 설치는 유료 OCI 리소스를 생성하지 않으며, 서버·저장 공간·트래픽은 기존 무료 할당 범위 내에서 운영해야 합니다. 무료 DNS 서비스와 인증서 발급 성공을 사전에 보장하지 않습니다.

검증은 다른 기기의 브라우저로 HTTPS 주소를 열어 인증서 경고 없이 로그인 화면이 나오는지 확인한 다음, 로그인하여 실제 기사와 수집 결과를 확인합니다. 인증 없이 `/api/dashboard`를 열면 401이 나와야 합니다. 서비스 상태만으로 외부 접속 성공을 판단하지 않습니다. 인증서 발급이 안 되면 검증을 끄지 말고 DNS·포트·Caddy 로그를 확인하세요.

```bash
sudo systemctl status caddy --no-pager
sudo journalctl -u caddy -n 50 --no-pager
```

`publish.sh`는 기존 사용자 지정 Caddy 설정이 있으면 중단합니다. 기본 또는 이전 Memory Brief 설정은 백업 후 업데이트합니다. Ubuntu의 기존 방화벽 규칙을 유지하면서 TCP 80/443만 추가하며 UFW 또는 iptables-persistent로 유지합니다. 운영 중인 VM에서 방화벽 관리 도구를 변경했다면 먼저 해당 정책을 검토하세요. 앱 자체는 계속 127.0.0.1:8000에서 실행되며 디자인 업데이트는 기존 `install.sh` 절차로 적용합니다.

Gemini 키가 이미 설정된 서버에서 이후 수정본을 적용할 때는 현재 `gemini-setup` 브랜치에서 `git pull --ff-only` 후 `sudo bash deploy/install.sh`를 실행합니다. 키 재등록은 필요하지 않습니다. 실패가 계속되면 `python3 deploy/gemini_status.py`로 키가 포함되지 않는 상태 결과를 확인하세요. 출력에 다음 시도 시각이 있으면 해당 시각 이후 `지금 수집하기`를 눌러야 AI 생성을 다시 시도합니다. 웹 새로고침은 저장된 결과만 읽습니다.
