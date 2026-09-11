# 🤝 Tempera 기여 및 협업 가이드 (Contributing Guide)

본 문서는 팀원 간 병렬 작업 충돌을 방지하고, 코드 품질 및 벤치마크의 객관성을 일관되게 유지하기 위한 협업 규칙입니다.

---

## 👥 영역별 역할 및 파일 소유권 (Ownership)

작업 영역 간의 불필요한 커플링과 충돌을 막기 위해 파일 소유권을 분리하여 작업합니다.

| 담당 영역 | 주 작업 디렉토리 및 파일 | 주요 책임 |
| :--- | :--- | :--- |
| **코어 (Core & Runner)** | `src/tempera/`, `tests/` | 공통 데이터 모델(`Event`, `Policy`, `Result`), E2E 러너, 독립 Evaluator, 파이프라인 |
| **타겟 환경 (Environments)** | `environments/`, `docker/` | 타겟별 데이터 정규화 Adapter/Observer, 컨테이너 베이스라인 초기화(Reset), Dockerfile 관리 |
| **시나리오/정책 (Scenarios)** | `scenarios/` | 모의 침투 목표(`scenario.yaml`)와 ROE 보안 정책(`policy.yaml`) 정의 |
| **분석/도구 (Tools & Scripts)** | `scripts/` | pass@k 통계 집계(`aggregate.py`), 프로비저닝 헬퍼 등 |

> **💡 기본 원칙**: 하나의 PR 또는 작업 단위에서는 **가급적 자신의 담당 영역만 수정**하여 브랜치 병합 충돌을 최소화합니다.

---

## 📐 아키텍처 불변 원칙 (Architectural Invariants)

코드를 수정하거나 새로운 기능을 추가할 때 반드시 지켜야 하는 원칙입니다:

1. **관측과 판정의 엄격한 분리 (Observe ≠ Evaluate)**
   - `environments/` 및 `observe/`(Gateway, DB Collector)는 외부 시스템에서 발생한 사실만 수집하여 공통 `Event` 규격으로 정규화합니다.
   - 관측 레이어에서 `is_success`, `violation`, `goal_achieved` 등의 가치 판정을 내리는 필드를 생성하거나 주입해서는 안 됩니다.
2. **사후 Replay 기반 독립 평가 (Evaluate)**
   - 평가는 오직 기록된 `events.jsonl`만을 재생(Replay)하여 진행합니다.
   - 에이전트의 자체 보고(Self-report)나 단순 HTTP 요청 횟수는 평가 근거로 삼지 않습니다.
   - **Goal(목표)**, **Progress(라이프사이클 단계)**, **ROE(규칙 준수)**는 상호 독립적으로 판정하며, 합산하여 단일 점수로 왜곡하지 않습니다.
3. **스키마 버전 관리**
   - `src/tempera/core/`의 데이터 구조(`Event`, `Policy`, `Result`, `RunConfig`)를 변경할 때는 스키마 버전과 관련된 입출력 직렬화 로직을 반드시 함께 갱신합니다.

---

## 🛠️ 작업 가이드라인

### 1. 새로운 시나리오 추가 (`scenarios/`)
- 시나리오 ID와 디렉토리 이름은 일치해야 합니다 (예: `scenarios/JS-007/`).
- 한 시나리오 폴더 안에는 반드시 분리된 두 YAML 파일이 위치해야 합니다:
  - `scenario.yaml`: 공격 목표(`goal`), 성공 조건(`success_when`), 라이프사이클 단계(`progress`), 시작 상태 등.
  - `policy.yaml`: 허용된 범위 및 ROE 제약 조건.
- `tempera.runner run` 실행 시 `--policy`를 명시하지 않으면 자동으로 `scenarios/<scenario>/policy.yaml`을 사용합니다. 명시할 경우 해당 경로는 반드시 존재해야 합니다.

### 2. 새로운 대상 환경 추가 (`environments/`)
- `environments/<env_name>/` 디렉토리를 생성합니다.
- `environment.yaml`: 타겟 메타데이터, 진입 URL, 라이프사이클 규칙 명시.
- `observer.py`: `BaseObserver` 프로토콜을 준수하여 HTTP/시스템 이벤트를 공통 `Event`로 정규화.
- `reset.py` / `adapter.py`: 각 실행 전후 상태를 검증 가능한 baseline으로 복구하는 reset 인터페이스 구현.

---

## 📝 커밋 및 PR 컨벤션 (Conventional Commits)

명확한 히스토리 추적을 위해 다음 커밋 메시지 형식을 준수합니다:

```text
<type>(<scope>): <설명>

예시:
feat(runner): add --policy option to specify policy file path
fix(agent): handle malformed JSON in parse_action
refactor(environments): separate target reset from scenario provisioning
docs: update README quickstart and contributing guide
```

- **타입(Type)**:
  - `feat`: 새로운 기능 추가
  - `fix`: 버그 수정
  - `refactor`: 동작 변경 없는 코드 리팩터링
  - `docs`: 문서 수정
  - `test`: 테스트 코드 추가 및 수정
  - `chore`: 빌드/패키지/의존성 설정 수정
- **스코프(Scope)**: `runner`, `agent`, `core`, `observe`, `evaluate`, `juice_shop` 등 변경 영역 명시.

---

## 🧪 검증 및 품질 관리 (Verification)

변경 사항을 `main` 브랜치에 병합하기 전 다음 사항을 확인합니다:

1. **테스트 및 문법 검증**:
   ```bash
   python -m pytest
   ```
2. **시나리오 및 정책 YAML 문법 확인**:
   새로 작성하거나 수정한 YAML 파일이 유효한 구문인지 확인합니다.
3. **E2E 스모크 확인**:
   코어나 러너를 수정한 경우, 기본 시나리오(`JS-001`)로 정상 작동을 1회 검증합니다:
   ```bat
   python -B -m tempera.runner run --scenario JS-001 --model qwen2.5:3b --provider ollama --upstream http://127.0.0.1:3001
   ```
