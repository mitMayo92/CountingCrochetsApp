FROM alpine:3.21

ARG PDFJS_VERSION=4.10.38

RUN apk add --no-cache python3 py3-aiohttp poppler-utils curl \
 && mkdir -p /opt/pdfjs \
 && curl -sSfL "https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-${PDFJS_VERSION}.tgz" \
    | tar xz -C /tmp \
 && mv /tmp/package/build /tmp/package/cmaps /tmp/package/standard_fonts /opt/pdfjs/ \
 && rm -rf /tmp/package \
 && apk del curl

COPY app /app
WORKDIR /app
CMD ["python3", "-u", "/app/server.py"]
