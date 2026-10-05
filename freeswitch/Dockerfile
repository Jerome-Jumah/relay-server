FROM debian:bookworm-slim AS builder
# FreeSWITCH 1.11.3 and compatible dependencies, resolved during reference review.
ARG FREESWITCH_REF=ef32e205295e29f034f1453ad245ba5efb07b94a
ARG SOFIA_REF=ad36ac8f755308e8b87f98a505e83d4e408e5cc3
ARG SPANDSP_REF=8f1e1646bdec99eac5fd2cd92c35563f736b9b89
ARG BUILD_JOBS=2
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential autoconf automake libtool libtool-bin \
    pkg-config cmake nasm yasm python3 \
    libssl-dev libtiff-dev libjpeg-dev libsqlite3-dev libcurl4-openssl-dev \
    libpcre2-dev libspeex-dev libspeexdsp-dev libopus-dev libedit-dev \
    uuid-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN git init sofia && cd sofia && \
    git remote add origin https://github.com/freeswitch/sofia-sip.git && \
    git fetch --depth 1 origin ${SOFIA_REF} && git checkout FETCH_HEAD && \
    ./autogen.sh && ./configure --prefix=/usr/local && \
    make -j${BUILD_JOBS} && make install && \
    mkdir -p /usr/local/share/licenses/sofia-sip && \
    cp COPYING /usr/local/share/licenses/sofia-sip/
RUN git init spandsp && cd spandsp && \
    git remote add origin https://github.com/freeswitch/spandsp.git && \
    git fetch --depth 1 origin ${SPANDSP_REF} && git checkout FETCH_HEAD && \
    ./bootstrap.sh && ./configure --prefix=/usr/local && \
    make -j${BUILD_JOBS} && make install && ldconfig && \
    mkdir -p /usr/local/share/licenses/spandsp && \
    cp COPYING /usr/local/share/licenses/spandsp/
RUN git init freeswitch && cd freeswitch && \
    git remote add origin https://github.com/signalwire/freeswitch.git && \
    git fetch --depth 1 origin ${FREESWITCH_REF} && git checkout FETCH_HEAD
COPY freeswitch/modules.conf /build/freeswitch/modules.conf
RUN cd freeswitch && ./bootstrap.sh -j && \
    ./configure --prefix=/usr/local/freeswitch --disable-core-odbc-support && \
    make -j${BUILD_JOBS} && make install && \
    mkdir -p /usr/local/share/licenses/relay-freeswitch && \
    cp LICENSE /usr/local/share/licenses/relay-freeswitch/ && \
    rm -rf /usr/local/freeswitch/conf /usr/local/freeswitch/htdocs

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 libssl3 libtiff6 libjpeg62-turbo libsqlite3-0 \
    libcurl4 libpcre2-8-0 libspeex1 libspeexdsp1 libopus0 libedit2 \
    libuuid1 zlib1g && rm -rf /var/lib/apt/lists/* && \
    useradd --system --uid 10001 --home /var/lib/freeswitch freeswitch && \
    mkdir -p /var/lib/freeswitch/db /var/lib/freeswitch/tls /var/lib/freeswitch/run /etc/freeswitch && \
    chown -R freeswitch:freeswitch /var/lib/freeswitch /etc/freeswitch
COPY --from=builder /usr/local/ /usr/local/
RUN ldconfig
ENV PATH="/usr/local/freeswitch/bin:${PATH}"
COPY scripts/configure.py scripts/health.py /opt/relay/
COPY freeswitch/conf/ /opt/relay/templates/
COPY freeswitch/entrypoint.sh /opt/relay/entrypoint.sh
USER freeswitch
EXPOSE 5066/tcp
ENTRYPOINT ["sh", "/opt/relay/entrypoint.sh"]
