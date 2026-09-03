# AgentAlgo backend — includes the TradingAgents engine for live runs
FROM python:3.12-slim

WORKDIR /srv
RUN pip install --no-cache-dir uv

COPY backend/requirements.txt .
RUN uv pip install --system -r requirements.txt

# Engine: pinned upstream release for engine-mode runs
RUN uv pip install --system "tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git"

COPY backend/app ./app

# IMPORTANT: single worker — RunManager state is in-process (see QUALITY_REPORT C3).
# Scale by running multiple compose replicas with a shared external DB instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
