# OpenClaw 설치 및 초기 설정 가이드

> **작성 기준 (2026.04.14)**
> [공식 사이트 (openclaw.ai)](https://docs.openclaw.ai/getting-started)
> [한국어 가이드 (open-claw.me)](https://open-claw.me/ko/guide/getting-started)

---

## 목차

1. [개요](#1-개요)
2. [설치 전 준비사항](#2-설치-전-준비사항)
3. [설치](#3-설치)
4. [초기 설정 (onboard)](#4-초기-설정-onboard)
5. [실행 및 채팅 플랫폼 연결](#5-실행-및-채팅-플랫폼-연결)
6. [점검 및 정상 동작 확인](#6-점검-및-정상-동작-확인)
7. [보안 및 운영 주의사항](#7-보안-및-운영-주의사항)
8. [문제 해결](#8-문제-해결)
9. [설치 요약: 최소 실행 순서](#9-설치-요약-최소-실행-순서)
10. [복붙용 최소 명령어 모음](#10-복붙용-최소-명령어-모음)
11. [참고 링크](#11-참고-링크)

---

## 1. 개요

**OpenClaw**는 내 컴퓨터에서 직접 실행되는 오픈소스 개인 AI 어시스턴트로, WhatsApp·Telegram·Discord 등 평소 쓰는 채팅 앱을 통해 명령을 내릴 수 있는 에이전트 도구입니다.

### 주요 기능


| 기능              | 설명                                                                                        |
| --------------- | ----------------------------------------------------------------------------------------- |
| **로컬 실행·모델 연동** | Mac, Windows, Linux에서 내 컴퓨터에 설치해 동작. OpenAI, Anthropic(Claude), 로컬 모델 등을 선택해 연결한다고 안내합니다. |
| **채팅 인터페이스**    | WhatsApp, Telegram, Discord, Slack, Signal, iMessage 등에서 대화로 명령합니다.                       |
| **장기 기억(메모리)**  | 세션·사용자 선호·맥락을 유지해 개인화한다고 설명합니다.                                                           |
| **브라우저 제어**     | 웹 검색, 폼 작성, 데이터 추출 등(문서의 Browser Control 등).                                              |
| **시스템 접근**      | 파일 읽기/쓰기, 셸 명령 실행 등(문서의 Full System Access / 샌드박스 선택 등 안내).                               |
| **스킬(Skills)**  | 커뮤니티 스킬을 쓰거나 직접 만들어 확장합니다.                                                                |
| **프로액티브 동작**    | 크론 작업, 알림, 백그라운드 태스크 등.                                                                   |


AI 모델은 OpenAI, Anthropic(Claude), 로컬 모델 등을 선택해서 연결할 수 있습니다.

---

## 2. 설치 전 준비사항


| 항목          | 조건                        | 비고                                         |
| ----------- | ------------------------- | ------------------------------------------ |
| Node.js     | **Node 24 권장**            | **Node 22.14+ 지원** (`node --version`으로 확인) |
| 운영체제        | macOS, Linux, Windows 지원원 | Windows(WSL2 권장)                           |
| AI 모델 API 키 | OpenAI 또는 Anthropic API 키 | 둘 중 하나 필수                                  |


### ⚠️ Windows 사용자 필독: WSL2 권장

> [시작 가이드](https://open-claw.me/ko/guide/getting-started): 호환성·성능을 위해 WSL2 사용을 **강력히 권장**.
> Windows에서 직접(PowerShell/CMD) 실행도 가능하지만, 가능하면 **WSL2(Ubuntu 등)** 터미널에서 아래 설치·설정 권장. 
>
> WSL2가 없다면 아래 방법으로 설치:  
> **WSL2 설치 방법 (Windows 10/11)**
>
> ```powershell
> # PowerShell 또는 명령 프롬프트를 관리자 권한으로 실행
> wsl --install
> ```
>
> 설치 후 재부팅하면 Ubuntu가 기본으로 설치됩니다. 이후 모든 명령어는 **WSL2 터미널 안에서** 실행하세요.
>
> ### Windows 기준 설치 전 준비 사항 (WSL2)
>
> 1. **WSL2 + 배포판 설치** (PowerShell **관리자** — [Microsoft WSL 설치](https://learn.microsoft.com/windows/wsl/install))
>
> ```powershell
> wsl --install
> # 또는
> wsl --list --online
> wsl --install -d Ubuntu-24.04
> ```
>
> 1. **(권장) WSL 안에서 systemd 사용** — 게이트웨이를 **systemd 사용자 서비스**로 설치하는 흐름과 맞물림. [공식 Windows 가이드](https://docs.openclaw.ai/platforms/windows)에 따라 WSL에서 `/etc/wsl.conf`에 `[boot]` / `systemd=true` 설정 후 `wsl --shutdown` 등으로 재시작하고, `systemctl --user status`로 확인.
> 2. WSL 터미널에서 **Node 22.14+ (권장: 24)** 확인.
> 3. 아래 **설치**의 **Linux 원라이너** 또는 **npm 전역 설치**로 OpenClaw 설치.
> 4. `openclaw onboard --install-daemon` 으로 초기 설정.
> 5. 게이트웨이·상태·채널 연결 순으로 진행.
>
> **경로 안내**: WSL에서는 `~/.openclaw/` 가 Linux 홈 아래 있음. Windows 탐색기에서는 `\\wsl$\<배포판>\home\<사용자>\` 등으로 접근.

---

> ### Windows 네이티브 기준 (WSL 미사용)
>
> ```powershell
> iwr -useb https://openclaw.ai/install.ps1 | iex
> ```
>
> - 네이티브 Windows도 지원되나,  **여전히 WSL2가 더 안정적인 경로** 권장.
> - `install.ps1` URL 접근 오류 시, [Install](https://docs.openclaw.ai/install)의 **npm·Docker 등 대안** 확인.
> **데몬(백그라운드) — 공식 문서 기준** ([Windows](https://docs.openclaw.ai/platforms/windows))
> - `openclaw onboard --install-daemon` / `openclaw gateway install` 등은 **Windows에서는 Scheduled Task 생성을 우선** 시도.
> - 생성이 거부 시시 **시작 프로그램(Startup) 폴더** 폴백 등 문서 참고.

---

> ### macOS / Linux 기준
>
> 1. 터미널에서 **Node 22.14+ (권장: 24)** 확인.
> 2. **원라이너** 또는 **npm 전역 설치** 중 선택.
> 3. `openclaw onboard --install-daemon`.
> 4. 게이트웨이·채널·점검 명령 순으로 진행.

---

## 3. 설치

#### 방법 A — 원라이너 (macOS / Linux / WSL)

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

- OpenClaw와 필요한 모든 종속성(Node.js 포함)을 자동으로 설치
- 원라이너는 Node.js와 모든 의존성을 자동 감지·설치. macOS 첫 실행 시 Homebrew 설치를 위해 관리자 권한 필요.

#### 방법 B — npm/pnpm (Node.js 환경이 이미 갖춰진 개발자용)

```bash
npm i -g openclaw
```

- npm 전역 패키지로 openclaw CLI를 설치
- `install.ps1` 실패 시 유용

```bash
pnpm add -g openclaw
```

- pnpm을 선호하는 경우

#### 방법 C — 소스에서 빌드

```bash
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --install-method git
또는
git clone https://github.com/openclaw/openclaw.git
cd openclaw && pnpm install && pnpm run build
```

- GitHub 소스를 직접 클론해서 빌드
- 코드를 직접 수정하거나 기여하고 싶은 경우에 사용

---

### 설치 후 (대시보드)

>  ****`openclaw` **CLI가 설치된 뒤**에 실행

```bash
openclaw dashboard
```

- 브라우저에서 **[http://127.0.0.1:18789/](http://127.0.0.1:18789/)** 로 컨트롤 UI에 접속. 
- 로컬 웹 UI로 설치·게이트웨이가 열렸는지 빠르게 확인 및 검증

---

## 4. 초기 설정 (onboard)

### Onboard 명령 실행

```bash
openclaw onboard --install-daemon
```

- 게이트웨이·모델 제공자(API 키 등)·채널·데몬을 마법사로 설정.

### 마법사가 순서대로 안내하는 항목

##### **1. 게이트웨이 유형 선택 (로컬/원격 등 메시지 처리 중심 서비스 구성)**

- **로컬 게이트웨이**: 내 컴퓨터에서 직접 실행. 기본값이자 입문자에게 권장.
- **원격 게이트웨이**: 서버에 배포하는 방식. 추가 설정 필요.

##### **2. AI 모델 인증 설정 (AI 모델(GPT, Claude 등)을 호출할 수 있도록 인증 정보 입력)**


| 선택지                | 방법                                    |
| ------------------ | ------------------------------------- |
| Anthropic (Claude) | API 키 입력 권장. `claude setup-token`도 지원 |
| OpenAI (GPT)       | OAuth 또는 API 키                        |


- **Claude Code가 이미 설치된 경우**: `claude setup-token` 명령으로 기존 자격 증명 재사용.

##### **3. 채팅 플랫폼 연결**

- **WhatsApp**: QR 코드 스캔 방식
- **Telegram / Discord**: 봇 토큰 입력 방식
- 마법사가 각 플랫폼별 안내를 제공합니다.

##### **4. 데몬 설치**

- 컴퓨터를 켜두는 동안 OpenClaw 게이트웨이를 자동으로 백그라운드에서 유지시켜 주는 서비스
- 컴퓨터를 재시작해도 OpenClaw가 자동으로 실행
- macOS launchd, Linux/WSL2 systemd, **Windows는 Scheduled Task 우선**

---

## 주요 설정 파일 / 디렉토리 위치


| 경로                                                       | 내용                     |
| -------------------------------------------------------- | ---------------------- |
| `~/.openclaw/`                                           | 기본 구성 디렉터리             |
| `~/.openclaw/credentials/oauth.json`                     | OAuth 자격 증명            |
| `~/.openclaw/agents/<agent_ID>/agent/auth-profiles.json` | 인증 프로필 (OAuth + API 키) |
| `~/.openclaw/credentials/whatsapp/` 등                    | WhatsApp 등 채널 자격 증명    |


- WhatsApp 현재 인증 파일명 등은 [WhatsApp 채널 문서](https://docs.openclaw.ai/channels/whatsapp)에 최신 경로가 나옵니다.

---

## 5. 실행 및 채팅 플랫폼 연결

### 게이트웨이 시작 및 상태 확인

```bash
# 게이트웨이 상태 확인
openclaw gateway status
# 게이트웨이 수동 시작 (포트 지정 시)
openclaw gateway --port 18789
# 전체 상태 빠른 확인
openclaw status
# 헬스 체크
openclaw health

# 웹 대시보드 주소
http://127.0.0.1:18789/
# 빠른시작 (대시보드 바로 열기)
openclaw dashboard
```

**디버그:**

- `openclaw status --all`
- `openclaw health` 또는 `openclaw status --deep`

---

### 채팅 플랫폼 연결

#### [WhatsApp](https://docs.openclaw.ai/channels/whatsapp)

```bash
openclaw channels login --channel whatsapp
```

- QR 후 **WhatsApp → 설정 → 연결된 기기**에서 스캔.
- **전용 번호** 운영을 권장.
- 페어링:

```bash
openclaw pairing list whatsapp
openclaw pairing approve whatsapp <CODE>
```

#### [Telegram](https://docs.openclaw.ai/channels/telegram)

- BotFather에서 봇 토큰을 만든 뒤 **설정 파일 또는 환경 변수** 사용.
- `openclaw channels login telegram` 방식은 사용 안함
- 게이트웨이 실행 후 DM 페어링:

```bash
openclaw gateway
openclaw pairing list telegram
openclaw pairing approve telegram <CODE>
```

#### [Discord](https://docs.openclaw.ai/channels/discord)

- Developer Portal에서 봇 토큰 발급 후, **채팅에 토큰을 붙여 넣지 말고** 실행 환경에 설정

```bash
export DISCORD_BOT_TOKEN="YOUR_BOT_TOKEN"
openclaw config set channels.discord.token --ref-provider default --ref-source env --ref-id DISCORD_BOT_TOKEN --dry-run
openclaw config set channels.discord.token --ref-provider default --ref-source env --ref-id DISCORD_BOT_TOKEN
openclaw config set channels.discord.enabled true --strict-json
openclaw gateway
```

```bash
openclaw pairing list discord
openclaw pairing approve discord <CODE>
```

---

## 6. 점검 및 정상 동작 확인

- CLI·게이트웨이·(선택) 메시지까지 최소 한 번 검증합니다.

```bash
openclaw --version
openclaw doctor
openclaw gateway status
openclaw health
```

- `openclaw health`에 **auth not configured** 가 보이면 온보딩으로 돌아가 API 키·OAuth를 설정.

**테스트 메시지 (예시)**

```bash
openclaw message send --target +15555550123 --message "Hello from OpenClaw"
```

### 보안·페어링 (DM)

- 기본적으로 **미승인 발신자는 pairing** 으로 처리.
- `openclaw pairing list <채널>` / `openclaw pairing approve ...` 로 승인.

---

## 7. 보안 및 운영 주의사항

- pairing·허용 목록 등으로 **접근 주체를 제한**.
- 봇 토큰·API 키는 **환경 변수·SecretRef** 등으로 관리 또는  `~/.openclaw/` 비공개.
- **WhatsApp 전용 번호 권장**: 기존 개인 번호보다 전용 번호 사용이 안전합니다.
- **VoIP 번호 비권장**: WhatsApp 정책상 VoIP 번호는 차단될 가능성이 높습니다.
- **페어링 승인**: 처음 연결 시 반드시 페어링을 승인해야 하며, 알 수 없는 요청은 승인하지 마세요.
- **허용 목록 설정**: 구성 파일에서 특정 번호를 자동 허용하도록 허용 목록을 설정할 수 있습니다.
- **데몬 관리**: 데몬이 설치된 경우 시스템 리소스(CPU, 메모리)를 지속적으로 사용합니다. 필요 없을 때는 중지하세요.
- **모델 비용**: API 키 방식 사용 시 AI 모델 호출 비용이 발생합니다. 사용량을 주기적으로 확인하세요.
- OpenClaw는 실제 작업을 수행할 수 있어, **계정·채널 권한을 최소화**하는 것이 안전.
- 특정 기업·산업의 내부 보안 정책(금지/허용)은 **조직 보안팀 기준**으로 별도 검토 필요.

---

## 8. 문제 해결

```bash
# 전체 상태 진단 보고서 (가장 먼저 실행)
openclaw status --all
 
# 일반적인 구성 문제 자동 복구
openclaw doctor
 
# 게이트웨이 및 RPC 연결 상태 확인
openclaw gateway status
 
# 실시간 로그 확인
openclaw logs --follow
```

### 해결 방안


| 증상                                         | 원인                                            | 해결책                                                                                                                                                              |
| ------------------------------------------ | --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openclaw health`에 **auth not configured** | API 키·OAuth 미설정                               | `openclaw onboard` 또는 `openclaw onboard --install-daemon`으로 재설정                                                                                                  |
| `openclaw`를 찾을 수 없음                        | PATH·전역 npm 경로 문제                             | `npm prefix -g`와 PATH 확인, 터미널 재시작                                                                                                                                |
| Node 관련 설치·실행 오류                           | Node **22.14 미만**(권장 **24**)                  | `node -v` 확인 후 요구 버전으로 업그레이드                                                                                                                                     |
| 게이트웨이가 안 뜸·포트 오류                           | 기본 포트(예: **18789**) 사용 중 또는 프로세스 미기동          | `openclaw gateway status` 확인 후 `openclaw gateway --port [다른 포트]` 등                                                                                               |
| WhatsApp QR 실패·연결 불안정                      | QR 만료·세션 불안정                                  | `openclaw channels login --channel whatsapp` 재시도, `openclaw channels status`, [WhatsApp 문서](https://docs.openclaw.ai/channels/whatsapp)의 `openclaw doctor`·로그 안내 |
| Telegram/Discord 봇 무응답                     | 토큰·`channels.*.enabled`·게이트웨이 미실행·**페어링 미승인** | 토큰·설정 확인 → `openclaw gateway` → `openclaw pairing list <채널>` → `openclaw pairing approve <채널> <CODE>`                                                            |
| **OAuth token refresh failed**             | 리프레시 토큰 만료 등                                  | `openclaw onboard`로 OAuth 재인증                                                                                                                                    |
| 환경 변수가 반영되지 않음                             | 셸 세션이 갱신되지 않음                                 | 터미널 재시작 또는 `source ~/.bashrc` / `~/.zshrc`                                                                                                                       |
| WSL2에서 명령만 안 됨                             | PATH 미적용                                      | `source ~/.bashrc` 또는 WSL 재시작                                                                                                                                    |
| Windows 네이티브에서 자주 불안정                      | 네이티브 쪽 제약                                     | 공식 권장인 **WSL2** 사용 검토                                                                                                                                            |
| 원인을 바로 알기 어려움                              | 설정·런타임 상태를 한눈에 볼 필요                           | `openclaw status --all` 출력 보관, `openclaw doctor`, `openclaw logs --follow`                                                                                       |


### 참고:

- [공식 FAQ 문서](https://docs.openclaw.ai/help/faq)
- [GitHub Discussions](https://github.com/openclaw/openclaw/discussions)
- [Discord 커뮤니티](https://discord.com/invite/clawd)

---

## 9. 설치 요약: 최소 실행 순서

### Windows 기준 권장 흐름

```
Step 1. PowerShell(관리자) 실행 → WSL2 설치
       wsl --install
       → 재부팅
 
Step 2. WSL2(Ubuntu) 터미널 실행 → OpenClaw 설치
       curl -fsSL https://openclaw.ai/install.sh | bash
 
Step 3. 초기 설정 마법사 실행 (데몬 포함)
       openclaw onboard --install-daemon
       → 게이트웨이 유형 선택 (로컬 권장)
       → API 키 입력 (Anthropic 또는 OpenAI)
       → 채팅 플랫폼 선택 및 연결
 
Step 4. 게이트웨이 상태 확인
       openclaw status
 
Step 5. 채팅 앱에서 테스트 메시지 전송
       openclaw message send --target [내 번호] --message "안녕"
```

### macOS / Linux 공통 흐름

```
Step 1. 터미널에서 원라이너 실행
       curl -fsSL https://openclaw.ai/install.sh | bash
 
Step 2. 초기 설정 마법사 실행
       openclaw onboard --install-daemon
 
Step 3. 게이트웨이 상태 확인
       openclaw status
 
Step 4. 채팅 앱 연결 후 테스트
       openclaw message send --target [번호] --message "안녕"
```

---

## 10. 복붙용 최소 명령어 모음

```bash
# ① 설치 (원라이너)
curl -fsSL https://openclaw.ai/install.sh | bash
 
# ② npm으로 설치 (Node.js 22+ 환경)
npm i -g openclaw
 
# ③ 초기 설정 (데몬 포함)
openclaw onboard --install-daemon
 
# ④ 대시보드 열기 (빠른 시작)
openclaw dashboard
 
# ⑤ 게이트웨이 상태 확인
openclaw gateway status
 
# ⑥ 헬스 체크
openclaw health
 
# ⑦ 전체 상태 진단
openclaw status --all
 
# ⑧ WhatsApp QR 연결
openclaw channels login
 
# ⑨ 페어링 목록 확인
openclaw pairing list whatsapp
 
# ⑩ 페어링 승인
openclaw pairing approve whatsapp <code>
 
# ⑪ 테스트 메시지 전송
openclaw message send --target +821012345678 --message "Hello from OpenClaw"
 
# ⑫ 실시간 로그 확인
openclaw logs --follow
 
# ⑬ 자동 문제 진단 및 복구
openclaw doctor
 
# ⑭ 보안 감사
openclaw security audit --deep
```

---

## 11. 참고 링크


| 항목                            | 링크                                                                                             |
| ----------------------------- | ---------------------------------------------------------------------------------------------- |
| 공식 홈페이지                       | [https://openclaw.ai/](https://openclaw.ai/)                                                   |
| 공식 문서 (Getting Started)       | [https://docs.openclaw.ai/getting-started](https://docs.openclaw.ai/getting-started)           |
| 한국어 가이드                       | [https://open-claw.me/ko/guide/getting-started](https://open-claw.me/ko/guide/getting-started) |
| GitHub                        | [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)                   |
| Windows (WSL2·네이티브·게이트웨이 서비스) | [https://docs.openclaw.ai/platforms/windows](https://docs.openclaw.ai/platforms/windows)       |
| 설치 (npm·Docker 등 대안 설치)       | [https://docs.openclaw.ai/install](https://docs.openclaw.ai/install)                           |
| 채널 문서                         | [https://docs.openclaw.ai/channels](https://docs.openclaw.ai/channels)                         |


---

> **⚠️ 주의**: 공식 문서와 설치 스크립트가 업데이트되면 **명령어·옵션·경로·권장 OS 구성이 달라질 수 있습니다.** 
>
> 설치 전 [openclaw.ai](https://openclaw.ai/) 및 [docs.openclaw.ai](https://docs.openclaw.ai/)의 최신 버전을 확인하세요.

