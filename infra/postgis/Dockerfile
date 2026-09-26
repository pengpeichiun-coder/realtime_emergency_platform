FROM --platform=$BUILDPLATFORM golang:1.27.1-alpine3.24@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS gosu-build

ARG TARGETOS
ARG TARGETARCH

RUN apk add --no-cache git ca-certificates \
    && GOOS=$TARGETOS GOARCH=$TARGETARCH CGO_ENABLED=0 \
       go install github.com/tianon/gosu@6456aaa0f3c854d199d0f037f068eb97515b7513 \
    && mkdir -p /out \
    && if [ -x "/go/bin/${TARGETOS}_${TARGETARCH}/gosu" ]; then \
         cp "/go/bin/${TARGETOS}_${TARGETARCH}/gosu" /out/gosu; \
       else \
         cp /go/bin/gosu /out/gosu; \
       fi

FROM postgis/postgis:17-3.6-alpine@sha256:a8ffa9afeea4ad6eada171fa2afdb57cd3eb90f92ce20156aa2cb8411d70e0cd

USER root

# Refresh Alpine security fixes while keeping the PostgreSQL/PostGIS image pinned.
RUN apk upgrade --no-cache

# The upstream image bundles an older gosu binary. Rebuild its pinned source for
# the target architecture so the existing PostGIS entrypoint remains compatible.
COPY --from=gosu-build /out/gosu /usr/local/bin/gosu
RUN chmod 0755 /usr/local/bin/gosu \
    && gosu --version \
    && gosu nobody true
