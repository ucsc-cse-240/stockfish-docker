FROM python:3.12-alpine3.20 AS builder

ARG PYTHON_CHESS_VERSION=1.1
ARG STOCKFISH_COMMIT=f8aa78e0a7e8853370e5989fc23783f3b244ac42
ARG STOCKFISH_ARCH=x86-64
ENV STOCKFISH_COMMIT=${STOCKFISH_COMMIT}

# Build Stockfish, install it, then remove the source and the build tools.
# Runtime libraries are installed later so `apk del` doesn't remove them.
WORKDIR /
RUN apk add --no-cache --virtual .build-deps git make g++ curl \
    && mkdir /src && cd /src \
    && git init -q \
    && git fetch -q --depth 1 https://github.com/official-stockfish/Stockfish.git "${STOCKFISH_COMMIT}" \
    && git checkout -q FETCH_HEAD \
    && cd src/ \
    && make -j"$(nproc)" net \
    && make -j"$(nproc)" build ARCH="${STOCKFISH_ARCH}" \
    && cp /src/src/stockfish /usr/bin/stockfish \
    && cd / && rm -rf /src \
    && apk del .build-deps

# Install only the runtime libraries Stockfish needs.
WORKDIR /app
RUN apk add --no-cache libstdc++ libgcc

# Install the entrypoint dependencies.
RUN pip install --no-cache-dir python-chess==${PYTHON_CHESS_VERSION}

COPY scripts/entrypoint.py .
RUN chmod 755 /app/entrypoint.py

VOLUME ["/games"]

ENTRYPOINT ["python3", "/app/entrypoint.py"]
