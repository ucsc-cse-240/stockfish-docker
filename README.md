# Stockfish Chess Game Analyzer (Docker)

A [Docker](https://en.wikipedia.org/wiki/Docker_(software)) image to compile and run the [Stockfish](https://github.com/official-stockfish/Stockfish) engine for playing chess.

## Running

The docker image is fairly standard:

You can build an image with teh tag `stockfish-engine`

`docker build -t stockfish-engine .`

Once built, the container 
Run the container by passing a FEN string, an optional comma-separated list of moves.

`docker run --rm stockfish-engine "<FEN>" "<MOVES>"`

### Example:

`docker run --rm stockfish-engine "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" "e4,e5,Nf3"`

## Understanding the Output

The `rating` field provides the engine's evaluation of the position:
* Integers (Centipawns): A value of 100 is roughly equivalent to a 1-pawn advantage for White. 
  Positive values favor White; negative values favor Black.
* Mate String (M#): When a forced checkmate is detected, the rating switches to a string format:
  * `M1`: White has a forced checkmate in 1 move.
  * `M-3`: Black has a forced checkmate in 3 moves (White is being mated).
  * `M0`: The current position is checkmate.

### Output Format

The container returns a JSON object to stdout, which can be redirected to a file.

``` 
{
    "initial_fen": "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
    "config": { "depth": 15 },
    "initial_rating": 52,
    "analysis_steps": [
        { "move": "Bb5", "rating": 45 }
    ],
    "final_rating": 45
} 
```

## Licensing

This repository is provided under the MIT license (see [LICENSE](https://github.com/ucsc-cse-240/stockfish-docker/blob/main/LICENSE)). 
Stockfish is covered under the [GPL-3](https://github.com/official-stockfish/Stockfish/blob/master/Copying.txt).
This project does not distribute the source or binary of Stockfish,
but does reference a specific Git commit hash to compile it as part of the Docker image.