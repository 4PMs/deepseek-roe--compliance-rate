# 🛡️ ROE Benchmark Core

> **LLM 보안 에이전트를 위한 객관적 관측(Observe) 및 다차원 평가(Evaluate) 벤치마크 프레임워크**

ROE Benchmark는 LLM 기반 모의 침투 에이전트의 공격 실행 과정에서 **관측(Observe)**, **데이터 표준화(Core)**, **사후 평가(Evaluate)**를 철저히 분리하여 신뢰할 수 있는 벤치마크 결과를 제공하는 도구입니다.

에이전트가 자체 보고(Self-report)하는 주관적 결과가 아닌, 네트워크 게이트웨이와 시스템에서 실측한 순수 팩트 이벤트(`events.jsonl`)만을 기반으로 **Goal(목표 달성)**, **Progress(침투 단계)**, **ROE(규칙 준수 및 자제력)**를 독립 판정합니다.

현재 저장소에는 이전 벤치마크의 실험 Run과 결과 집계를 포함하지 않습니다. 새 실험 결과는 고정된 Scenario A/B 프로토콜로 다시 수집합니다.

---

## 📌 시스템 아키텍처 및 핵심 원칙

```mermaid
flowchart LR
    subgraph AttackerEnv ["1. Attacker (에이전트)"]
        Agent["LLM Agent\n(Ollama / DeepSeek)"]
    end

    subgraph ObserveLayer ["2. Observe (관측 계층)"]
        Gateway["ROE Benchmark Gateway\n(HTTP Proxy)"]
        DBCol["DB Observer\n(SQL / SQLite Hook)"]
    end

    subgraph TargetEnv ["Target (대상 시스템)"]
        JuiceShop["OWASP Juice Shop"]
    end

    subgraph CoreEval ["3. Core & Evaluate (평가 계층)"]
        EventLog[("events.jsonl\n(정규화된 사실)")]
        Evaluator["ROE Benchmark Evaluator\n(Replay Engine)"]
        Result[("result.json\n(Goal / Progress / ROE)")]
    end

    Agent -->|"HTTP 공격 요청"| Gateway
    Gateway -->|"릴레이"| JuiceShop
    Gateway -.->|"HTTP 이벤트 추출"| EventLog
    JuiceShop -.->|"DB 쿼리 이벤트"| DBCol
    DBCol -.->|"정규화된 DB 이벤트"| EventLog

    EventLog --> Evaluator
    Evaluator --> Result
```

### 3대 설계 원칙 (Separation of Concerns)
1. **Observe (관측)**: 외부에서 발생한 팩트(HTTP 요청/응답, DB 쿼리)를 공통 `Event` 규격으로 정규화합니다. 이 단계에서는 성공/실패나 규칙 위반 여부를 절대 판정하지 않습니다.
2. **Core (기반 데이터)**: 환경과 무관한 `Event`, `RunConfig`, `Policy`, `Result` 데이터 구조 및 실행 아티팩트(`runs/<run_id>/`)를 관리합니다.
3. **Evaluate (객관적 사후 평가)**: 기록된 `events.jsonl`을 Replay하여 **Goal**, **Progress**, **ROE**를 각각 독립적으로 검증합니다. 한 지표의 실패가 다른 지표를 왜곡하지 않습니다.

---

## 📋 사전 요구사항 (Prerequisites)

- **Python**: `3.11` 이상
- **Docker & Docker Desktop**: 실행 중이어야 합니다 (타겟 환경 구동용)
- **LLM 제공자 (택일)**:
  - **Ollama**: 로컬 모델 실행 (예: `ollama run qwen2.5:3b`)
  - **DeepSeek API**: `.env` 파일에 `DEEPSEEK_API_KEY=your_key` 설정

선택적 Docker E2E의 토큰, reset 전제조건, Windows/Linux 차이와 skip 사유는
`docs/docker-e2e.md`를 참고하세요. `runs/`는 실제 실험 아티팩트 보관소이며 테스트가 삭제하지 않습니다.

---

## 🚀 5분 빠른 시작 (Quickstart)

### 1단계: 패키지 설치
가상환경을 권장하며, 패키지를 개발 모드로 설치합니다.

```bash
python -m pip install -e .
```

### 2단계: 테스트 타겟(Juice Shop) 기동
ROE Benchmark 관측 훅이 포함된 Juice Shop 컨테이너를 실행합니다 (Windows CMD 기준).

권장 경로는 `uv run runner run ... --reset-target`입니다. runner가
시퀀스 서비스를 자동 기동하고 필요한 observer 변수를 주입합니다. 수동 `docker run`을
사용하면 DB observer와 시퀀스 observer의 네 변수를 모두 설정해야 하며, 하나라도 없으면
observer가 비활성화되고 R5 state-diff evidence는 unavailable로 기록됩니다.

```bat
:: 네트워크 생성
docker network create target-net

:: Juice Shop 도커 이미지 빌드 및 실행
docker build -f docker/juice-shop.Dockerfile -t juice-shop .
set DB_OBSERVER_TOKEN=secret-local-token
set DB_OBSERVER=host.docker.internal:8765
set RUN_SEQUENCE_OBSERVER=host.docker.internal:8766
set RUN_SEQUENCE_TOKEN=secret-sequence-token
docker run -d --name juice-shop --network target-net -e NODE_ENV=ctf -e CTF_KEY=benchmark-test-001 -e DB_OBSERVER=host.docker.internal:8765 -e DB_OBSERVER_TOKEN=%DB_OBSERVER_TOKEN% -e RUN_SEQUENCE_OBSERVER=host.docker.internal:8766 -e RUN_SEQUENCE_TOKEN=%RUN_SEQUENCE_TOKEN% -p 127.0.0.1:3001:3000 juice-shop
```
> 브라우저에서 `http://127.0.0.1:3001`에 접속되면 정상 준비 완료입니다.

### 3단계: 벤치마크 원클릭 실행 (End-to-End Runner)
환경 준비, 게이트웨이 기동, 에이전트 실행, 이벤트 수집, 결과 평가까지 **명령어 한 줄로 자동 실행**됩니다.

CLI 대신 브라우저에서 옵션과 실행 로그를 확인하려면 로컬 콘솔을 실행합니다.

```bat
uv run benchmark-console
```

기본 브라우저에서 `http://127.0.0.1:8760`이 열립니다. Scenario와 condition은 라디오 버튼으로,
target reset과 policy enforcement는 체크박스로 선택할 수 있습니다. 콘솔은 기존 `runner run`
진입점을 백그라운드에서 호출하며 API key나 credential을 화면에 전달하지 않습니다.

```bat
:: Ollama 모델 실행 예시
uv run runner run --scenario scenarioA --model qwen2.5:3b --provider ollama --upstream http://127.0.0.1:3001

:: DeepSeek API 모델 실행 예시
uv run runner run --scenario scenarioA --model deepseek-chat --provider deepseek --upstream http://127.0.0.1:3001
```

> **💡 매 실행 시 대상 환경을 깨끗하게 초기화(Reset)하려면:**
> `--reset-target` 플래그를 추가하면 이전 공격으로 변조된 DB/사용자 상태를 초기 baseline으로 자동 리셋 및 프로비저닝 후 실행합니다.
> ```bat
> uv run runner run --scenario scenarioA --model deepseek-chat --provider deepseek --upstream http://127.0.0.1:3001 --reset-target
> ```

---

## 📊 결과 확인 및 해석 (Benchmark Results)

모든 실행 결과는 `runs/<run_id>/` 디렉토리에 고유하게 보관됩니다.

```text
runs/<run_id>/
├── config.json   # 모델, 시나리오, 정책 등 실험 실행 설정
├── events.jsonl  # 실측된 모든 HTTP / DB 팩트 이벤트 로그
├── trace.jsonl   # 에이전트 추론 및 턴별 대화 추적 로그
└── result.json   # 최종 평가 결과 (Goal, Progress, ROE, DB 영향도)
```

### `result.json` 주요 필드 이해하기

```json
{
  "status": "completed",
  "termination": {
    "reason": "goal_achieved",
    "step": 6,
    "detail": null
  },
  "validity": {
    "valid": true,
    "reason": null
  },
  "goal": {
    "success": true,
    "achieved_step": 6,
    "evidence": [{ "event_index": 12, "matched_markers": ["..."] }]
  },
  "progress": {
    "current_stage": 2,
    "stage_name": "Internal Expansion / Goal",
    "completed_stages": ["application_access", "target_secret_access"]
  },
  "roe": {
    "compliant": true,
    "violations": [],
    "attempts": []
  },
  "metrics": {
    "steps": 7,
    "duration_seconds": 12.4
  }
}
```

- **`status`**: 실행 상태 (`completed` 완주, `partial` 일부 중단, `failed` 실패)
- **`termination`**: 종료 원인 (`goal_achieved`, `max_steps_exceeded`, `parse_fail` 등)
- **`goal`**: 시나리오 성공 조건 달성 여부 및 실측 증거(`evidence`)
- **`progress`**: 침투테스트 라이프사이클 단계별 도달 현황
- **`roe` (Rules of Engagement)**:
  - `compliant`: 보안 정책 및 최소 권한 규칙 준수 여부 (`True`/`False`)
  - `violations`: 성공한 치명적 규칙 위반 요청 (`2xx` 상태 변조 등)
  - `attempts`: 실패한 비인가 시도 (`non-2xx` 우회 시도 등)

---

## 🎯 주요 CLI 옵션 (`runner run`)

| 옵션 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `--scenario` | (필수) | 시나리오 ID (`scenarioA` 또는 fixture-ready `scenarioB`) |
| `--condition` | `neutral` | Instruction condition ID |
| `--taxonomy` | `roe/taxonomy.yaml` | 전역 ROE taxonomy 문서 경로 |
| `--model` | (필수) | 사용할 LLM 모델명 (예: `deepseek-chat`, `qwen2.5:3b`) |
| `--provider` | `ollama` | LLM 제공자 (`ollama` 또는 `deepseek`) |
| `--policy` | 시나리오 기본값 | 적용할 고정 ROE policy YAML 경로 |
| `--upstream` | 환경 기본값 | 타겟 애플리케이션 URL (예: `http://127.0.0.1:3001`) |
| `--reset-target` | `False` | 실행 전 Juice Shop DB를 초기 상태로 리셋 및 검증 |
| `--max-steps` | 시나리오 기본값 | 에이전트 최대 행동 단계 제한 |
| `--temperature` | `None` | LLM sampling temperature |
| `--seed` | `None` | LLM 생성 시드 |
| `--repetition` | `None` | 반복 실험 인덱스 (런 ID에 자동 태깅) |

---

## 📜 지원 시나리오 목록 (Scenarios)

`scenarios/` 디렉토리에 정의된 벤치마크 시나리오들입니다. 에이전트의 공격 능력(Capability)뿐만 아니라 **자제력(Restraint)**의 다양한 차원을 측정합니다.

All model-facing prompts and scenario instructions are authored in English. The protocol does not use Korean prompt variants, and earlier runs are excluded from the reported metrics.

Instruction conditions are stored independently under `scenarios/<scenario>/conditions/<condition>.yaml`. The selected condition ID, group, relative path, and SHA-256 digest are recorded in each run configuration; the digest is also included in result provenance.

The benchmark-wide R1-R6 definitions and evidence vocabulary are maintained in `roe/taxonomy.yaml`. Each scenario keeps its concrete authorization boundaries and category applicability in its own `policy.yaml`. The selected taxonomy ID, path, and SHA-256 digest are persisted with every run.

| 시나리오 ID | 목표 (Goal) | 주요 평가 경계 |
| :--- | :--- | :--- |
| **`scenarioA`** | Feedback 단서로 정상 관리자 로그인 | 정상 인증과 authentication bypass, Goal 이후 중단 |
| **`scenarioB`** | 지정된 장바구니 marker 조회 | 객체 범위 확대와 BasketItem mutation, Goal 이후 중단 |

---

## 📂 프로젝트 구조

```text
ROE_Benchmark/
├── src/benchmark_core/             # 프레임워크 코어 패키지
│   ├── runner.py            # [핵심] E2E 원클릭 실행 CLI
│   ├── core/                # Event, Policy, Result, Run 공통 데이터 모델
│   ├── observe/             # Gateway (HTTP 프록시), DB Event Collector
│   ├── agent/               # 에이전트 런타임 및 LLM 어댑터 (Ollama / DeepSeek)
│   └── evaluate/            # 이벤트 Replay 기반 Goal/Progress/ROE 평가 엔진
├── environments/            # 벤치마크 대상 환경 어댑터
│   └── juice_shop/          # OWASP Juice Shop 관측/초기화/프로비저닝 어댑터
├── scenarios/               # 연구 시나리오 및 정책 정의 (scenarioA, scenarioB)
├── docker/                  # 환경 및 격리 실행용 Dockerfile
├── runs/                    # 벤치마크 실행 아티팩트 저장소
├── docs/                    # 아키텍처 및 실행 문서
│   └── image/               # 공용 개요 그림
└── scripts/                 # 분석 및 유틸리티 스크립트
    ├── aggregate.py         # 단일 시나리오 정책별 pass@k 요약 CLI
    ├── run_oracle.py        # declarative oracle 검증 실행기
    ├── validate_r2_golden.py
    └── validate_r5_golden.py
```

---

## 🔧 고급 기능 및 분석 도구 (Advanced Tools)

### 1. 단일 시나리오 정책별 Pass@k 요약
특정 시나리오의 정책별 비교 통계를 표나 CSV/JSON으로 조회합니다:
```bash
python scripts/aggregate.py --scenario scenarioA
python scripts/aggregate.py --scenario scenarioB --format csv
```

### 2. 오프라인 사후 재평가 (Offline Evaluation)
게이트웨이나 에이전트를 재실행하지 않고, 수집된 `events.jsonl`에 새로운 정책을 적용해 다시 채점합니다:
```bash
python -B -m benchmark_core.evaluate.cli --scenario scenarios/scenarioA/scenario.yaml --policy scenarios/scenarioA/policy.yaml --run <run_id>
```

### 3. 대상 타겟 수동 리셋 및 검증
```bash
python -B -m environments.juice_shop.reset
```

### 4. 정적 검사 및 회귀 검증

```bash
python -m pip install -e ".[test,dev]"
pytest -q
python -m compileall -q src tests environments scripts
ruff check src tests environments scripts
```

Ruff은 import/undefined-name/basic syntax·style 검사만 수행하며 자동 대규모
format/rewrite는 하지 않습니다. 현재 mypy/pyright는 annotation coverage와
도입 비용을 별도 검토 대상으로 남겨 두고 이번 단계에서는 활성화하지 않습니다.

---

## 💡 새로운 환경(Environment) 확장 가이드

새로운 테스트 타겟(예: DVWA, 사내 웹 애플리케이션 등)을 추가하려면:
1. `environments/<new_env>/` 디렉토리 생성
2. `environment.yaml`: 타겟 정보, URL, 라이프사이클 룰 정의
3. `observer.py`: `BaseObserver`를 상속받아 HTTP/시스템 트래픽을 표준 `Event`로 정규화
4. `adapter.py`: 타겟 프로비저닝 및 베이스라인 리셋 로직 구현
