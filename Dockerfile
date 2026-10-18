FROM python:3.12-slim
WORKDIR /app
COPY server.py delivery.py *.html logo.png ./
COPY assets ./assets
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV HOST=0.0.0.0 PORT=4180 ARHANUD_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 4180
CMD ["python", "server.py"]
