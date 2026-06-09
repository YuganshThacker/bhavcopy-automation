# Portable image (Railway / any container host). Render's `runtime: python`
# uses requirements.txt directly and does not need this, but it's here so the
# same repo deploys anywhere.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default command runs today's pipeline. On Railway, set this as the cron
# service start command and configure the schedule in the Railway dashboard.
CMD ["python", "run_daily.py"]
