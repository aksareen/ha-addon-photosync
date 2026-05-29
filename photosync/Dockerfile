ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-flask \
    rclone \
    jq \
    bash \
    curl

COPY rootfs/ /
COPY run.sh /
RUN chmod a+x /run.sh

WORKDIR /app

CMD ["/run.sh"]
