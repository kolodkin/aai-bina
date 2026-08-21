# syntax=docker/dockerfile:1
# queryview image: QueryView API server + bundled SPA.
# Build-arg QUERYVIEW_VERSION pins the wheel to install from PyPI.
FROM python:3.13-slim

ARG QUERYVIEW_VERSION
ARG PIP_INDEX_URL=https://pypi.org/simple/

LABEL org.opencontainers.image.source="https://github.com/kolodkin/aai-bina"
LABEL org.opencontainers.image.description="QueryView API server + SPA"

RUN pip install --no-cache-dir \
    --index-url "${PIP_INDEX_URL}" \
    "queryview==${QUERYVIEW_VERSION}"

# Run as a non-root user.
RUN useradd --create-home --uid 1000 queryview
WORKDIR /home/queryview
USER queryview

# The default DB path sits inside site-packages, which the non-root user can't
# write; keep the SQLite file (and its sibling .key file) in the home dir so a
# volume mounted at /home/queryview persists all state.
ENV DB_PATH=/home/queryview/queryview.db

EXPOSE 8000

# Health probe uses stdlib (slim image has no curl).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["queryview"]
