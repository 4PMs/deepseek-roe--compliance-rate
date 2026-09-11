# ROE Benchmark Agent Control Dashboard

ROE Benchmark 실행 아티팩트를 읽어 AI 레드팀 에이전트의 **Goal 수행과 RoE 준수 여부를 증거 중심으로 탐색**하는 read-only 대시보드입니다.

이 화면은 Juice Shop의 취약점을 진단하는 스캐너가 아닙니다. 기본 화면은 개별 Run의 관측 이벤트, ATT&CK TTP, Goal 도달점, RoE 위반 구간을 하나의 시간선으로 보여줍니다. 전체 ATT&CK Matrix는 보조 분석 화면입니다.

## 실행

외부 패키지 없이 Python 3.11+ 표준 라이브러리만 사용합니다.

### 합성 데모 데이터

```bash
python -m dashboard.app --demo
```

브라우저에서 <http://127.0.0.1:8766>을 엽니다. `dashboard/fixtures/demo_runs/`의 데이터는 UI 검증용 합성 자료이며 실제 실험 결과가 아닙니다.

### 실제 ROE Benchmark 결과

저장소 루트에서 실행하면 기본적으로 `runs/`를 읽습니다.

```bash
python -m dashboard.app
```

다른 경로를 사용할 수도 있습니다.

```bash
python -m dashboard.app --runs-dir /path/to/runs --port 8766
```

## 입력 계약

```text
runs/<run_id>/
├── config.json       # 필수
├── events.jsonl      # 관측된 사실
├── trace.jsonl       # 에이전트 thought/action/observation
├── result.json       # Goal/Progress/RoE 평가
├── episode_error.txt # 선택
└── condition.json    # 대시보드 sidecar, 선택
```

`condition.json` 예시:

```json
{
  "condition_id": "access-denial-escalation",
  "pressure": {
    "family": "access_denial",
    "level": 2
  }
}
```

없는 값은 성공·준수로 추정하지 않고 `unknown`으로 보존합니다. `result.json`이 없으면 Goal과 RoE가 `unknown`이며 해당 Run은 유효 행동 통계의 기본 분모에서 제외됩니다.

## 구조

```text
dashboard/
├── app.py                         # read-only HTTP API + 정적 UI 서버
├── backend/
│   ├── adapters/benchmark_v01.py    # ROE Benchmark 0.1 artifact adapter
│   ├── domain/models.py           # UI와 독립된 안정적 내부 모델
│   └── services/
│       ├── analytics.py           # 유효 Run 기준 Goal/RoE 집계
│       ├── attack_mapper.py       # 사실 이벤트 → ATT&CK 매핑
│       └── run_loader.py          # runs 디렉터리 탐색
├── frontend/                      # dependency-free HTML/CSS/JS
├── mappings/attack/               # versioned ATT&CK mapping rules
├── fixtures/demo_runs/            # 명시적 합성 데모
└── tests/                          # adapter/metric/UI contract tests
```

데이터 흐름:

```text
ROE Benchmark JSON/JSONL
      ↓ read-only
BenchmarkV01Adapter
      ↓
DashboardRun / TimelineEvent
      ├── ATT&CK attribution
      ├── Goal marker
      └── RoE violation evidence
      ↓
API → TTP/RoE Timeline → secondary ATT&CK Matrix
```

ATT&CK 귀속과 RoE 판정은 같은 factual event를 참조하지만 서로 독립적입니다. Technique이 관측됐다는 이유만으로 위반으로 처리하지 않습니다.

## 현재 ATT&CK 매핑

`mappings/attack/juice_shop_v1.json`에서 관리합니다.

| 관측 근거 | Technique | 상태 |
|---|---|---|
| `authentication_bypass` | T1190 | HTTP 2xx면 verified, 그 외 attempted |
| `path_traversal` | T1190 | HTTP 2xx면 verified, 그 외 attempted |
| business DB INSERT/UPDATE/DELETE | T1565.001 | verified |

모든 HTTP 요청을 억지로 Technique에 연결하지 않습니다. 매핑되지 않은 이벤트는 `UNMAPPED OBSERVATION`으로 보입니다.

## 테스트

```bash
python -m unittest discover -s dashboard/tests -v
node --check dashboard/frontend/app.js
```

## 현재 범위

포함:

- 실시간 artifact 새로고침
- Run 선택 및 capability/control 결과
- 이벤트 기반 TTP/RoE timeline
- 위반 evidence 목록
- 유효 Run 분모가 명시된 비교표
- metric toggle이 있는 secondary ATT&CK Matrix
- malformed/missing artifact warning 보존

아직 포함하지 않음:

- 실험 실행/중단 제어
- 인증 또는 멀티유저 저장소
- ATT&CK Navigator Layer export
- 통계적 신뢰구간과 pressure curve
- 코어에 없는 안정적 `event_id` 및 구조화된 termination reason
