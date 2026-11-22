FROM python:3.12-slim

RUN useradd --create-home --uid 1000 tagscope
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

USER tagscope
WORKDIR /data
EXPOSE 8000
ENTRYPOINT ["tagscope"]
# Mount a config at /data/tagscope.yaml, or run the demo:
#   docker run -p 8000:8000 tagscope demo --host 0.0.0.0 --no-browser
CMD ["run", "/data/tagscope.yaml", "--host", "0.0.0.0"]
