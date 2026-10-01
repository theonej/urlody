# Container image for the scorer API (see deploy/ for the Cloud Run deployment).
#
#   docker build -t scorer .
#   docker run -p 8080:8080 --env-file .env scorer
FROM python:3.12-slim

# uv installs the locked dependency set reproducibly; the audio and music
# libraries all ship manylinux wheels (ffmpeg comes bundled in imageio-ffmpeg),
# so no system packages are needed.
RUN pip install --no-cache-dir uv==0.11.15

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache

ENV PATH="/app/.venv/bin:$PATH" \
    SCORER_API_HOST=0.0.0.0 \
    PORT=8080 \
    NUMBA_CACHE_DIR=/tmp/numba

EXPOSE 8080
CMD ["scorer-api"]
