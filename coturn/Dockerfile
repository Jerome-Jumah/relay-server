FROM debian:bookworm-slim AS builder
# coturn 4.18.0; no external database clients compiled in.
ARG COTURN_REF=23c6c1d32a3d2b21a56cee65d3203bcc4ea82d2b
ARG BUILD_JOBS=2
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential libssl-dev libevent-dev pkg-config && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN git init coturn && cd coturn && \
    git remote add origin https://github.com/coturn/coturn.git && \
    git fetch --depth 1 origin ${COTURN_REF} && git checkout FETCH_HEAD && \
    ./configure --prefix=/usr/local --sysconfdir=/etc/coturn && \
    make -j${BUILD_JOBS} && make install && \
    mkdir -p /usr/local/share/licenses/coturn && \
    cp LICENSE /usr/local/share/licenses/coturn/
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 libssl3 libevent-2.1-7 libevent-core-2.1-7 libevent-extra-2.1-7 \
    libevent-openssl-2.1-7 libevent-pthreads-2.1-7 && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --system --uid 10002 --home /tmp coturn && \
    mkdir -p /etc/coturn && chown coturn:coturn /etc/coturn
COPY --from=builder /usr/local/ /usr/local/
COPY scripts/configure.py scripts/health.py /opt/relay/
COPY coturn/entrypoint.sh /opt/relay/entrypoint.sh
USER coturn
ENTRYPOINT ["sh", "/opt/relay/entrypoint.sh"]
