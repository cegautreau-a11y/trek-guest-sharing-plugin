FROM python:3.12-alpine

WORKDIR /srv
COPY public /srv/public
RUN cp /srv/public/config.js.example /srv/public/config.js
COPY server /srv/server

RUN mkdir -p /cache /run/secrets \
    && chown -R 65532:65532 /srv /cache /run/secrets \
    && chmod -R go-w /srv

USER 65532:65532
EXPOSE 8080
ENV PUBLIC_ROOT=/srv/public LISTEN_PORT=8080
CMD ["python", "/srv/server/server.py"]
