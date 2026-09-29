FROM python:3.12-bookworm

ARG SNUETL_UID=1000
ARG SNUETL_GID=1000
ARG SNUETL_SOURCE_REVISION=unknown
ARG SNUETL_PACKAGE_VERSION=unknown

LABEL org.opencontainers.image.revision="${SNUETL_SOURCE_REVISION}" \
      org.opencontainers.image.version="${SNUETL_PACKAGE_VERSION}"

ENV HOME=/home/snuetl \
    XDG_CONFIG_HOME=/home/snuetl/.config \
    XDG_STATE_HOME=/home/snuetl/.local/state \
    XDG_CACHE_HOME=/tmp/snuetl-cache \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG DEBIAN_FRONTEND=noninteractive

RUN groupadd --gid "${SNUETL_GID}" snuetl \
    && useradd --uid "${SNUETL_UID}" --gid "${SNUETL_GID}" --create-home --shell /bin/bash snuetl

WORKDIR /opt/snuetl

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && python -m playwright install --with-deps firefox \
    && apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX "${PLAYWRIGHT_BROWSERS_PATH}" \
    && install -d -o snuetl -g snuetl -m 0700 \
        /home/snuetl/.config/snuetl \
        /home/snuetl/.local/state/snuetl \
        /data/downloads \
        /data/videos

USER snuetl

CMD ["python", "-m", "snuetl.container_runtime"]
