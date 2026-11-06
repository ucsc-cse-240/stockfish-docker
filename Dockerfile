# TODO: Maybe make it a single stage build
ARG PYTHON_CHESS_VERSION=1.1
ARG CHESSAI_VERSION=0.0.5

# --- STAGE 1: The Builder ---
FROM python:3.12-alpine3.20 AS builder

# Install build tools
RUN apk add --no-cache git make g++ sed

# Clone the official Stockfish repository
ARG STOCKFISH_COMMIT=f8aa78e0a7e8853370e5989fc23783f3b244ac42
WORKDIR /src
RUN git clone https://github.com/official-stockfish/Stockfish.git . \
    && git checkout ${STOCKFISH_COMMIT}

# Move to the source directory and compile
WORKDIR /src/src
RUN make -j net
RUN make -j build

# --- STAGE 2: The Final Image ---
FROM python:3.12-alpine3.20

ARG STOCKFISH_COMMIT=f8aa78e0a7e8853370e5989fc23783f3b244ac42
ARG PYTHON_CHESS_VERSION
ARG CHESSAI_VERSION
ENV STOCKFISH_COMMIT=${STOCKFISH_COMMIT}

# Install only the runtime libraries Stockfish needs
RUN apk add --no-cache libstdc++ libgcc

# Copy the compiled binary from the builder stage
# Standard Stockfish build creates the binary in /src/src/
COPY --from=builder /src/src/stockfish /usr/bin/stockfish

WORKDIR /app

# TODO: Consider not importing chessai and just make sure the output is correct.
RUN pip install --no-cache-dir \
    python-chess==${PYTHON_CHESS_VERSION} \
    ucsc-chessai==${CHESSAI_VERSION}

COPY scripts/entrypoint.py .
RUN chmod 755 /app/entrypoint.py

VOLUME ["/games"]

ENTRYPOINT ["python", "/app/entrypoint.py"]
