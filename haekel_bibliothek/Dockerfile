FROM alpine:3.21

ARG PDFJS_VERSION=4.10.38

RUN apk add --no-cache python3 py3-aiohttp poppler-utils curl \
 && mkdir -p /opt/pdfjs \
 && curl -sSfL "https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-${PDFJS_VERSION}.tgz" \
    | tar xz -C /tmp \
 && mv /tmp/package/build /tmp/package/cmaps /tmp/package/standard_fonts /opt/pdfjs/ \
 && rm -rf /tmp/package \
 && apk del curl

# Link-Import: Webseite im Lesemodus (readability) als PDF (WeasyPrint)
RUN apk add --no-cache py3-lxml py3-cffi py3-pip pango font-dejavu font-noto \
 && apk add --no-cache --virtual .build build-base python3-dev libffi-dev \
 && pip install --no-cache-dir --break-system-packages \
      weasyprint==70.0 readability-lxml==0.9 lxml_html_clean==0.4.6 \
 && apk del .build

COPY app /app
WORKDIR /app
CMD ["python3", "-u", "/app/server.py"]
