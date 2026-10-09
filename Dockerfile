# Multi-stage: build the UI, then a slim Python runtime that serves API + UI.
# data/app.db (built by the pipeline) is copied in. The raw dump and Parquet are not needed at runtime.
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY salesintel/ salesintel/
COPY config/ config/
COPY prompts/ prompts/
COPY evals/ evals/
COPY data/app.db data/app.db
COPY --from=web /web/dist web/dist
ENV HOST=0.0.0.0 PORT=8000 SI_LLM_DAILY_BUDGET_USD=2
EXPOSE 8000
CMD ["python", "-m", "salesintel.api"]
