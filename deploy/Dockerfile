# Live backend for TraceNum. Build context = the folder produced by deploy/package_runtime.py
# (frozen v2 subset + backend code + embedding model), never the whole project.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/hf HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    TRACENUM_RERANKER=siliconflow TRACENUM_TORCH_THREADS=2 \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
    TRACENUM_CACHE_DIR=/data/cache TRACENUM_DATA_DIR=/data

WORKDIR /app
COPY requirements-runtime.txt /tmp/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.1 \
 && pip install -r /tmp/requirements.txt

COPY git-shim.sh /usr/local/bin/git
RUN chmod 755 /usr/local/bin/git && useradd --create-home --uid 10001 tracenum \
 && mkdir -p /data && chown tracenum /data
COPY --chown=root:root hf /app/hf
COPY --chown=root:root v2 /app/v2
COPY --chown=root:root formal_reports_2025 /app/formal_reports_2025
COPY --chown=root:root deepseek_workspace /app/deepseek_workspace
COPY --chown=root:root tracenum /app/tracenum
COPY RUNTIME.json /app/RUNTIME.json

# Run logs and caches go to the /data volume; the frozen tree stays read-only, except the
# Chroma folder: chromadb opens its SQLite file read-write even for queries. It is the
# image's own copy (rebuilt from the locked bytes on every deploy), and corpus_routing
# still verifies collection identity, metadata hashes and chunk counts on every run.
RUN ln -s /data/runs /app/tracenum/backend/runs && ln -s /data/recordings /app/tracenum/backend/recordings \
 && chown -R tracenum /app/v2/vector_db_formal_v1
USER tracenum
WORKDIR /app/tracenum/backend
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).status == 200 else 1)"
# Port 8765 is only reachable inside the compose network (Caddy is the sole public entry),
# which is why forwarded headers from any peer on that network are trusted.
CMD ["sh", "-c", "mkdir -p /data/runs /data/recordings /data/cache && exec python -m uvicorn server:app --host 0.0.0.0 --port 8765 --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
