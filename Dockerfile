FROM debian:bookworm-slim AS unfs3-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake bison ca-certificates flex gcc git libc6-dev libtirpc-dev make pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src/unfs3
RUN git clone https://github.com/unfs3/unfs3.git . \
    && git checkout --detach ec1660ba33c80d5c67131e163e68834c1a10e243 \
    && ./bootstrap && ./configure && make -j2

FROM debian:bookworm-slim

LABEL org.opencontainers.image.title="ShareCoveX" \
      org.opencontainers.image.description="Publish existing host folders over SMB, NFS, or both." \
      org.opencontainers.image.source="https://github.com/MacRimi/ShareCoveX" \
      org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 samba samba-common-bin samba-vfs-modules avahi-daemon libtirpc3 tini acl \
    nfs-ganesha nfs-ganesha-vfs rpcbind \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=unfs3-builder /src/unfs3/unfsd /usr/local/sbin/unfsd
COPY --from=unfs3-builder /src/unfs3/LICENSE /usr/share/doc/sharecovex/UNFS3-LICENSE
COPY sharecovex /app/sharecovex
COPY web /app/web
RUN mkdir -p /config /shares /run/samba /run/ganesha /run/rpcbind /var/lib/samba/private \
    && ln -sf /proc/mounts /etc/mtab
EXPOSE 8080 445 2049 20048
VOLUME ["/config"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)"]
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "-m", "sharecovex"]
