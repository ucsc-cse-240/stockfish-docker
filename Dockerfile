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

# Install only the runtime libraries Stockfish needs
RUN apk add --no-cache libstdc++ libgcc

# Copy the compiled binary from the builder stage
# Standard Stockfish build creates the binary in /src/src/
COPY --from=builder /src/src/stockfish /usr/bin/stockfish

WORKDIR /app

RUN pip install --no-cache-dir python-chess==1.1

COPY scripts/entrypoint.py .

ENTRYPOINT ["python", "entrypoint.py"]