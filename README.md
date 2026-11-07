# Stockfish Chess Game Analyzer (Docker)

A [Docker](https://en.wikipedia.org/wiki/Docker_(software)) image that compiles a pinned [Stockfish](https://github.com/official-stockfish/Stockfish) engine and analyzes chess positions and PGNs.
The engine binary is installed only inside the image at `/usr/bin/stockfish`; callers do not provide an engine binary or path.

## Running

Build an image with the tag `stockfish-engine`:

```sh
docker build -t stockfish-engine .
```

### FEN mode

Pass a FEN string and, optionally, a comma-separated list of SAN moves.
The container writes a JSON object to stdout.

```sh
docker run --rm stockfish-engine "<FEN>" "<MOVES>" --depth 15
```

For example, we can analyze the starting board with three simple moves played:

```sh
docker run --rm stockfish-engine "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" "e4,e5,Nf3"
```

### Batch PGN mode

Omit the FEN to analyze every `.pgn` file recursively under `/games`.
Mount the tournament output directory read/write because successful PGNs are rewritten in place.

```sh
docker run --rm --user "$(id -u):$(id -g)" -v "$TOURNAMENT_OUTPUT:/games" stockfish-engine --depth 15
```

The command writes a machine-readable JSON summary to stdout.
`--user "$(id -u):$(id -g)"` preserves ownership of rewritten host files.
It continues after individual file failures and exits nonzero if any file failed.
An empty mount also exits nonzero unless `--allow-empty` is passed.

Every game in a multi-game PGN is supported.
Only its played mainline is analyzed; variations are preserved but not evaluated.

## Understanding the Output

The `rating` fields provide the engine's evaluation of a position from White's perspective:
* Decimal pawns: A value of `1.00` is roughly equivalent to a 1-pawn advantage for White.
  Positive values favor White; negative values favor Black.
* Mate String (M#): When a forced checkmate is detected, the rating switches to a string format:
  * `M1`: White has a forced checkmate in 1 move.
  * `-M3`: Black has a forced checkmate in 3 moves (White is being mated).
  * `M0`: The current position is checkmate.

### Output Format

The container returns a JSON object to stdout, which can be redirected to a file.

``` json
{
    "initial_fen": "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "config": { "depth": 15 },
    "initial_rating": 0.52,
    "analysis_steps": [
        0.45
    ],
    "final_rating": 0.45
} 
```

## PGN annotations

Batch mode adds or replaces the `ChessAIStockfishAnalysis` PGN header.
Its value is compact JSON, escaped as required by PGN tags.
The object contains this schema:

```json
{
  "score_perspective": "white",
  "engine": {"name": "Stockfish", "commit": "<pinned image commit>"},
  "depth": 15,
  "annotations": [
    0.2,
    0.31,
    "M3",
  ]
}
```

`annotations[0]` is the initial position; each later annotation is the position after its mainline ply.
Numeric evaluations are signed pawns; mate values use the `M<n>` form.
Re-running the container replaces the existing header rather than accumulating annotations.

The Dockerfile defines `PYTHON_CHESS_VERSION` at its top. Use `--build-arg` to override
that pin when building an image. The container does not install ChessAI; it writes the
versioned JSON schema that ChessAI's game-rating annotation converter consumes.

## Licensing

This repository is provided under the MIT license (see [LICENSE](https://github.com/ucsc-cse-240/stockfish-docker/blob/main/LICENSE)).
Stockfish is covered under the [GPL-3](https://github.com/official-stockfish/Stockfish/blob/master/Copying.txt).
This project does not distribute the source or binary of Stockfish,
but does reference a specific Git commit hash to compile it as part of the Docker image.
