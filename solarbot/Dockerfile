# syntax=docker/dockerfile:1.7
#
# SolarBot build of ThingsBoard CE: the official tb-node image with ONLY the
# web UI (ui-ngx) rebuilt from this branch. The backend (every other entry of
# thingsboard.jar, the JRE, the scripts, the config) stays byte-identical to
# the official image, so the DB schema and server behaviour are upstream's.
#
#   docker buildx build -f solarbot/Dockerfile .        (context: repo root)
#
# CI (.github/workflows/solarbot-image.yml) passes TB_BASE_IMAGE pinned by
# digest; the default tag here is for a manual build.

ARG TB_VERSION=4.3.1.4
ARG TB_BASE_IMAGE=thingsboard/tb-node:${TB_VERSION}

# --- A: the UI, built the way ui-ngx/pom.xml builds it -----------------------
# Node and yarn versions from the frontend-maven-plugin config in
# ui-ngx/pom.xml (nodeVersion v22.22.2, yarnVersion v1.22.22, which this image
# ships). The UI is plain JS/CSS, so it is built once on the build platform.
FROM --platform=$BUILDPLATFORM node:22.22.2-bookworm AS ui
WORKDIR /src/ui-ngx
# Dependencies first (cached while only sources change). patches/ is needed
# by the "prepare" script (patch-package) that yarn install runs.
COPY ui-ngx/package.json ui-ngx/yarn.lock ui-ngx/.yarnrc ./
COPY ui-ngx/patches ./patches
# pom.xml's arguments, plus --frozen-lockfile: a lockfile that would need
# changing fails the build instead of silently resolving other versions.
RUN yarn install --non-interactive --check-files --frozen-lockfile \
      --network-concurrency 4 --network-timeout 100000
COPY ui-ngx/ ./
# "build:prod" = ng build --configuration production; output (angular.json)
# goes to target/generated-resources/public, the dir pom.xml packs as public/.
RUN yarn run build:prod \
 && grep -q '<title>SolarBot</title>' target/generated-resources/public/index.html

# --- B: swap the nested ui-ngx jar inside the official thingsboard.jar --------
FROM ${TB_BASE_IMAGE} AS official

FROM --platform=$BUILDPLATFORM python:3.13-slim-bookworm AS overlay
WORKDIR /work
COPY --from=official /usr/share/thingsboard/bin/thingsboard.jar official.jar
COPY --from=ui /src/ui-ngx/target/generated-resources/public public/
COPY solarbot/overlay_ui.py ./
# Writes the new ui-ngx jar STORED (Spring Boot cannot load a compressed
# nested jar) and self-checks every other entry is copied byte for byte.
RUN python3 overlay_ui.py official.jar public thingsboard.jar

# --- final: the official image plus one layer ---------------------------------
FROM ${TB_BASE_IMAGE}
ARG TB_VERSION
ARG TB_BASE_IMAGE
ARG IMAGE_VERSION=${TB_VERSION}-solarbot.dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="tb-node (SolarBot)" \
      org.opencontainers.image.description="ThingsBoard CE ${TB_VERSION} tb-node with the SolarBot-branded web UI; server identical to the official image" \
      org.opencontainers.image.source="https://github.com/solarbotdev/thingsboard" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="SolarBot" \
      org.opencontainers.image.base.name="${TB_BASE_IMAGE}"
USER root
# Overwrite the jar in place (cat >) instead of COPY: owner thingsboard and
# mode 0555 stay exactly as the official package installed them, and the
# layer holds one copy of the jar. The checks fail the build if either drifted.
RUN --mount=type=bind,from=overlay,source=/work,target=/mnt/solarbot \
    set -e; \
    jar=/usr/share/thingsboard/bin/thingsboard.jar; \
    before="$(stat -c '%U:%G %a' "$jar")"; \
    cat /mnt/solarbot/thingsboard.jar > "$jar"; \
    after="$(stat -c '%U:%G %a' "$jar")"; \
    [ "$before" = "$after" ] || { echo "owner/mode changed: $before -> $after" >&2; exit 1; }; \
    cmp /mnt/solarbot/thingsboard.jar "$jar"; \
    echo "thingsboard.jar replaced ($after)"
# Same user as the official image (its config: "User": "thingsboard").
USER thingsboard
