FROM node:24.21.0-bookworm-slim AS pi
WORKDIR /pi
COPY --chmod=644 harness/pi/package.json harness/pi/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund
COPY --chmod=644 harness/pi/*.mjs ./

FROM ubuntu:24.04 AS freecad
ARG FREECAD_VERSION=1.1.3
ARG FREECAD_SHA256=3a853eb69ee595f779f2255dbf80a765926981d8ff68903cefee4dfb03a8f5ef
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /download
RUN curl -fL --retry 3 "https://github.com/FreeCAD/FreeCAD/releases/download/${FREECAD_VERSION}/FreeCAD_${FREECAD_VERSION}-Linux-x86_64-py311.AppImage" -o FreeCAD.AppImage \
    && echo "${FREECAD_SHA256}  FreeCAD.AppImage" | sha256sum -c - \
    && chmod +x FreeCAD.AppImage && ./FreeCAD.AppImage --appimage-extract >/dev/null \
    && rm FreeCAD.AppImage

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates xvfb openbox openscad bubblewrap util-linux \
    poppler-utils fonts-dejavu-core libgl1 libegl1 libopengl0 libxrender1 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-xinerama0 libglu1-mesa dbus-x11 \
    && rm -rf /var/lib/apt/lists/* \
    && userdel -r ubuntu && useradd --create-home --uid 1000 cadpilot
WORKDIR /opt/cadpilot
COPY harness/requirements-lock.txt /tmp/requirements-lock.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements-lock.txt
ENV PATH=/opt/venv/bin:$PATH PLAYWRIGHT_BROWSERS_PATH=/opt/chromium
RUN python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
COPY --from=freecad /download/squashfs-root /opt/cadpilot/harness/apps/freecad-extracted
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /pi /opt/cadpilot/harness/pi
COPY src/cad1000 src/cad1000
COPY harness/server harness/server
COPY harness/web harness/web
COPY harness/knowledge/*.md harness/knowledge/
COPY harness/config.yaml harness/config.yaml
COPY harness/docker/entrypoint.sh /usr/local/bin/cadpilot-entrypoint
COPY harness/docker/smoke.py harness/docker/smoke.py
RUN chmod -R a+rX src harness/server harness/web harness/knowledge \
    && chmod 644 harness/config.yaml \
    && mkdir -p harness/state harness/projects harness/apps/openscad-extracted \
    && printf '#!/bin/sh\nexec /usr/bin/openscad "$@"\n' > harness/apps/openscad-extracted/AppRun \
    && chmod 755 harness/apps/openscad-extracted/AppRun /usr/local/bin/cadpilot-entrypoint \
    && chown -R cadpilot:cadpilot harness/state harness/projects harness/knowledge
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 QT_X11_NO_MITSHM=1 LIBGL_ALWAYS_SOFTWARE=1
USER cadpilot
WORKDIR /opt/cadpilot/harness
EXPOSE 7800
ENTRYPOINT ["cadpilot-entrypoint"]
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7800", "--ws-max-size", "65536", "--no-proxy-headers"]
