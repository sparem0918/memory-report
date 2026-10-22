# 오라클 서버에서 운영하기

이 파일은 **배포 준비 안내**입니다. 오라클 서버에 실제 설치하거나 공개한 상태를 뜻하지 않습니다. Ubuntu 또는 Oracle Linux 계열의 systemd 서버에서 Python 3.11 이상이 필요합니다. x86 서버와 ARM 서버 모두에 사용할 수 있으며 외부 Python 패키지를 설치하지 않습니다.

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

코드를 수정한 새 압축 파일을 전달한 뒤 같은 설치 절차를 반복합니다. 설치기는 기존 DB를 유지하며 새 릴리스로 전환합니다. 시작 확인에 실패하면 이전 코드로 되돌립니다. 앱 코드가 기존 데이터 구조를 바꾸는 업데이트라면 먼저 별도의 마이그레이션 검토가 필요합니다. 백업과 이전 릴리스는 자동 삭제하지 않습니다.

서버와 저장 공간이 본인의 오라클 무료 할당 범위에 포함되는지는 오라클 콘솔에서 확인하세요. 이 설치 절차는 새 VM이나 유료 클라우드 리소스를 생성하지 않습니다.
