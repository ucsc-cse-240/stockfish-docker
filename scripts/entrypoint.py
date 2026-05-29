"""
This script is the entry point for a docker container with the stockfish engine. 
It will take a board and moveline and output the rating for the board
as it performs each of the moves from the moveline.
"""

import argparse
import json
import os
import shutil
import sys
import typing

import chess
import chess.engine

DEFAULT_DEPTH_LIMIT = 15

def init_engine(engine_path: str) -> chess.engine.SimpleEngine:
    """ Initialize a chess engine binary using the UCI protocol. """

    # Clean the input target path.
    target_path = os.path.normpath(engine_path)

    # Locate the executable.
    executable_path = shutil.which(target_path)
    if (not executable_path):
        print(json.dumps({
            "error": f"Could not locate an executable binary matching requirement: '{target_path}'"
        }))
        sys.exit(1)

    return chess.engine.SimpleEngine.popen_uci(executable_path)

def get_rating_value(
        engine: chess.engine,
        board: chess.Board,
        depth_limit: int
        ) -> typing.Union[str, int]:
    """
    This function fetches an evaluation from the stockfish engine,
    then returns a simplified string or integer.
    """

    # Get the rating from the provided chess engine.
    info = engine.analyse(board, chess.engine.Limit(depth = depth_limit))
    score = info["score"].white()

    if (score.is_mate()):
        # If it is mate this will return the mate string (e.g., M-1).
        rating = f"M{score.mate()}"
    else:
        # It is not mate so this will return an integer rating value.
        rating = score.score()

    return rating

def get_moveline_ratings(
        engine: chess.engine,
        board: chess.Board,
        depth_limit: int, moves: str
        ) -> typing.List[typing.Dict[str, typing.Any]]:
    """""
    Parses a comma-separated string, updates the board position, and returns a move-by-move
    log of rating values.
    """""

    steps_history: typing.List[typing.Dict[str, typing.Any]] = []

    # Parse the movelist string.
    move_list: typing.List[str] = []
    for raw_move in moves.split(","):
        clean_move = raw_move.strip()

        # Filter out all empty string/whitespaces.
        if (clean_move):
            move_list.append(clean_move)

    # Verify the movelist contains all valid moves.
    for move_san in move_list:
        try:
            verified_move = board.parse_san(move_san)
        except ValueError:
            steps_history.append({
                "move": move_san, 
                "error": "Invalid move"
            })
            break

        # Update the board with the next move.
        board.push(verified_move)

        # Add the rating for the board after the current move.
        step_evaluation = get_rating_value(engine, board, depth_limit)
        steps_history.append({
            "move": move_san,
            "rating": step_evaluation
        })

    return steps_history

def main() -> None:
    """ This takes an input board and moveline, then returns the rating for each stage. """

    parser = argparse.ArgumentParser(
            description = 'Analyze a chess board position and a sequence of moves using Stockfish.')

    parser.add_argument('fen',
            action = 'store', type = str,
            help = 'The initial board position in FEN format.')

    parser.add_argument('moves',
            action = 'store', type = str, nargs = '?', default = None,
            help = "Comma-separated string of SAN moves to play sequentially (e.g., 'e4,e5,Nf3').")

    parser.add_argument('--depth', dest = 'depth',
            action = 'store', type = int, default = DEFAULT_DEPTH_LIMIT,
            help = 'The search depth limit for the engine evaluation (default: %(default)s).')

    parser.add_argument('--binary', dest = 'binary',
            action = 'store', type = str, default = 'stockfish',
            help = ('Custom file path to the Stockfish executable.'
                    + ' If a bare name is given, searches $PATH (default: %(default)s).'))

    args = parser.parse_args()

    output = {
        "initial_fen": args.fen,
        "config": {"depth": args.depth},
        "analysis_steps": []
    }

    # Verify the FEN.
    try:
        board = chess.Board(args.fen)
    except ValueError:
        print(json.dumps({"error": f"Invalid FEN string provided: '{args.fen}'"}))
        sys.exit(1)

    # Initialize the engine.
    try:
        engine = init_engine(args.binary)
    except (PermissionError, OSError) as e:
        print(json.dumps(
            {'error': f'OS rejected execution of engine binary at {args.binary}: {e.strerror}'}
        ))
        sys.exit(1)

    # Run the engine.
    try:
        # Initial Rating
        output["initial_rating"] = get_rating_value(engine, board, args.depth)

        # Movelist Rating
        output["analysis_steps"] = get_moveline_ratings(engine, board, args.depth, args.moves)

        # Final Rating
        output["final_rating"] = get_rating_value(engine, board, args.depth)
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError) as e:
        output["error"] = str(e)
    finally:
        engine.quit()

    # Final JSON output to stdout.
    print(json.dumps(output, indent = 4))

if __name__ == "__main__":
    main()
