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

FROM ubuntu:24.04 AS blender
ARG BLENDER_SERIES=4.2
ARG BLENDER_VERSION=4.2.9
ARG BLENDER_SHA256=dfbc127a7d28f9c2175b23bf9d6701b2855f31eedfb391f9a6e60adb24572846
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl xz-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /download
# download.blender.org (the origin) is heavily rate-limited to near-zero, and inside Docker's NAT
# network some mirrors collapse too (Clarkson: 17 MB/s on the host but 0.5 MB/s in a container),
# while ftp.halifax.rwth-aachen.de holds ~10 MB/s in-container. So try the container-fast mirror
# first, and `--speed-limit`/`--speed-time` abandon ANY source that can't sustain ~0.8 MB/s and
# fall through to the next - so a slow mirror never traps the build. The origin is the last resort
# WITHOUT a speed floor, so the build still completes (slowly) if every mirror is unreachable. The
# sha256 check guarantees the exact official file whichever source answers.
RUN (curl -fL --connect-timeout 10 --speed-limit 800000 --speed-time 20 "https://ftp.halifax.rwth-aachen.de/blender/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o blender.tar.xz \
     || curl -fL --connect-timeout 10 --speed-limit 800000 --speed-time 20 "https://mirrors.ocf.berkeley.edu/blender/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o blender.tar.xz \
     || curl -fL --connect-timeout 10 --speed-limit 800000 --speed-time 20 "https://mirror.clarkson.edu/blender/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o blender.tar.xz \
     || curl -fL --retry 3 --connect-timeout 15 "https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" -o blender.tar.xz) \
    && echo "${BLENDER_SHA256}  blender.tar.xz" | sha256sum -c - \
    && tar xf blender.tar.xz && mv "blender-${BLENDER_VERSION}-linux-x64" blender-extracted && rm blender.tar.xz

# MB-Lab: a parametric human/anime character generator (GPL, base mesh derived from MakeHuman CC0).
# Provides anatomically-correct, topologized, rigged human and anime bases for the Blender engine,
# so characters start from a professional base instead of scripted primitives. Placed as a Blender
# addon under a dedicated scripts dir that the sandbox exposes via BLENDER_USER_SCRIPTS.
FROM ubuntu:24.04 AS mblab
ARG MBLAB_REF=1_8_1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip && rm -rf /var/lib/apt/lists/*
WORKDIR /download
RUN curl -fL --retry 3 --connect-timeout 15 "https://github.com/animate1978/MB-Lab/archive/refs/tags/${MBLAB_REF}.zip" -o mblab.zip \
    && unzip -q mblab.zip && mkdir -p /mblab/addons && mv "MB-Lab-${MBLAB_REF}" /mblab/addons/MB_Lab && rm mblab.zip \
    # The self-updater writes a scratch dir/json inside its own addon folder on enable, which
    # crashes registration when that folder is a read-only sandbox mount. Redirect that scratch
    # path to /tmp (writable) so the addon loads; the network updater is unused in the sandbox anyway.
    && sed -i 's|os.path.dirname(__file__), self._addon + "_updater")|"/tmp", self._addon + "_updater")|' /mblab/addons/MB_Lab/addon_updater.py

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
COPY --from=blender /download/blender-extracted /opt/cadpilot/harness/apps/blender-extracted
COPY --from=mblab /mblab /opt/cadpilot/harness/apps/mblab
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
