# Image for running extract.py as a Cloud Run job (see docs/cloud-run.md).
# Input texts and outputs are not baked in: mount them (GCS volume on Cloud Run, -v locally).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Only what extract.py needs (versions from requirements.lock)
RUN pip install --no-cache-dir google-genai==2.10.0 pydantic==2.13.4 python-dotenv==1.2.2

COPY extract.py ./
COPY prompts/ ./prompts/

ENTRYPOINT ["python", "extract.py", "--input-dir", "/mnt/data/input", "--output-dir", "/mnt/data/output"]
