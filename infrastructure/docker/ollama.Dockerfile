# Ollama service — wrapper entrypoint that ensures the model is pulled
# before serving. The model is cached in the ollama_models volume, so
# subsequent container starts skip the pull and serve immediately.
FROM ollama/ollama:latest

# Runtime wrapper: pull model once (if not already cached in volume),
# then exec into the official serve command.
COPY infrastructure/docker/ollama/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 11434

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
