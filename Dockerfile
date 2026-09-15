FROM python:3.12-slim

WORKDIR /app

# libpq-dev is needed to build psycopg2 (Postgres driver) if you're not
# using the -binary wheel; build-essential covers any other C extensions
# in your requirements.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

# Hugging Face Spaces (Docker SDK) routes all public traffic to this one
# port — this must match "app_port" in README.md's frontmatter.
EXPOSE 7860

CMD ["./start.sh"]