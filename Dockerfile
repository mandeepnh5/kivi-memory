# Optional fallback. The primary review method is local Python; see RUN.md.
# This exists so the submission is still runnable on a machine where the
# local path is awkward (no Python 3.11+, or an externally managed
# interpreter that refuses pip installs).

FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so edits to the source do not invalidate this layer.
# Only the web demo needs them; the core, CLI, judge and eval are stdlib-only.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the seed persona into the image so the demo works the moment the
# container starts. The database lives in the container's writable layer and
# not a volume, so a fresh `docker run` always begins from the seed. Note that
# `docker restart` keeps that layer and therefore keeps whatever was learned;
# use a new container, or `docker exec ... python -m app.cli reset`, to reset.
RUN python -m app.cli reset

# The server binds loopback by default, which is unreachable from outside a
# container; KIVI_HOST is the documented override.
ENV KIVI_HOST=0.0.0.0
EXPOSE 8000

CMD ["python", "-m", "app.cli", "serve"]
