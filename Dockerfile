FROM python:3.11-slim

WORKDIR /app

COPY agent/requirements.txt agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt

COPY . .

# Railway's Docker-based routing needs a declared port to know where to
# send public traffic — Nixpacks/Railpack builds infer this automatically
# from the start command, but a raw Dockerfile doesn't get that for free.
EXPOSE 8080
ENV PORT=8080

# Shell form (not exec form) so $PORT expands from the runtime environment
# Railway injects — exec-form CMD arrays don't do shell variable expansion.
# The cron service overrides this with its own Custom Start Command
# (python -m agent.jobs.run_all) in Railway's dashboard.
CMD uvicorn agent.web.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*"
