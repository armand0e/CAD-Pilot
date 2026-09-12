FROM python:3.12-slim
WORKDIR /opt/cadpilot
COPY harness/requirements-lock.txt /tmp/requirements-lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements-lock.txt && useradd --create-home --uid 1000 cadpilot
COPY src/cad1000/*.py src/cad1000/
COPY harness/server harness/server
COPY harness/web harness/web
COPY harness/knowledge/*.md harness/knowledge/
COPY harness/config.yaml harness/requirements-lock.txt harness/
COPY harness/pi/*.mjs harness/pi/package.json harness/pi/package-lock.json harness/pi/
COPY harness/docker/Dockerfile harness/docker/entrypoint.sh harness/docker/smoke.py harness/docker/
COPY .dockerignore .dockerignore
RUN chmod -R a+rX src harness && mkdir -p harness/state && chown -R cadpilot:cadpilot harness/state
USER cadpilot
WORKDIR /opt/cadpilot/harness
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 7800
CMD ["uvicorn", "server.portal:app", "--host", "0.0.0.0", "--port", "7800", "--ws-max-size", "16777216", "--no-proxy-headers"]
