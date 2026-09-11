# 어태커 컨테이너 (Ubuntu 24.04, 팀 규격)
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1

# policy와 모델 API 호출에 필요한 최소 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-requests python3-yaml curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src ./src
ENV PYTHONPATH=/app/src

# scenario는 이미지에 넣지 않고 실행할 때 마운트한다.
#   예: -v %cd%\scenarios:/app/scenarios
# 에이전트 로직만 담는다. 모델은 밖(call_llm).
ENTRYPOINT ["python3", "-B", "-m", "benchmark_core.agent"]
