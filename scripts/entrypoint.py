"""
This script is the entry point for a Docker container with the Stockfish engine.
It will take a board and moveline and output the rating for the board
as it performs each of the moves from the moveline.
"""

import argparse
import json
import os
import sys
import tempfile
import typing

import chess
import chess.engine
import chess.pgn

DEFAULT_DEPTH_LIMIT = 15
GAMES_DIR = '/games'
STOCKFISH_PATH = '/usr/bin/stockfish'
ANNOTATION_HEADER = 'ChessAIStockfishAnalysis'
ANNOTATION_SCHEMA_VERSION = 1

Rating = int | str

def init_engine() -> chess.engine.SimpleEngine:
    """ Start the Stockfish binary installed in this image. """

    return chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)

def get_rating_value(engine: chess.engine.SimpleEngine, board: chess.Board,
                     depth_limit: int) -> Rating:
    """
    This function fetches an evaluation from the stockfish engine,
    then returns a simplified string or integer.
    """

    info = engine.analyse(board, chess.engine.Limit(depth = depth_limit))
    score = info['score'].white()

    if (score.is_mate()):
        return f'M{score.mate()}'

    rating = score.score()
    if (rating is None):
        raise chess.engine.EngineError('Stockfish returned a score without a centipawn value.')

    return rating

def get_moveline_ratings(
        engine: chess.engine.SimpleEngine,
        board: chess.Board,
        depth_limit: int, moves: str | None
        ) -> list[dict[str, typing.Any]]:
    """""
    Parses a comma-separated string, updates the board position, and returns a move-by-move
    log of rating values.
    """""

    if (moves is None):
        return []

    steps_history: list[dict[str, typing.Any]] = []

    # Parse the movelist string.
    move_list: list[str] = []
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

def analyze_fen(engine: chess.engine.SimpleEngine, fen: str, moves: str | None,
                depth_limit: int) -> dict[str, typing.Any]:
    """ Analyze one FEN and its optional comma-separated SAN move line. """

    try:
        board = chess.Board(fen)
    except ValueError as error:
        raise ValueError(f'Invalid FEN string provided: {fen!r}') from error

    output: dict[str, typing.Any] = {
        'initial_fen': fen,
        'config': {'depth': depth_limit},
    }

    output['initial_rating'] = get_rating_value(engine, board, depth_limit)
    output['analysis_steps'] = get_moveline_ratings(engine, board, depth_limit, moves)
    output['final_rating'] = get_rating_value(engine, board, depth_limit)

    return output

def _annotation(ratings: list[Rating], depth_limit: int) -> str:
    """ Build the compact JSON stored in an analyzed PGN's custom header. """

    data = {
        'schema_version': ANNOTATION_SCHEMA_VERSION,
        'score_perspective': 'white',
        'engine': {
            'name': 'Stockfish',
            'commit': os.environ.get('STOCKFISH_COMMIT', 'unknown'),
        },
        'depth': depth_limit,
        'ratings': ratings,
    }
    raw_value = json.dumps(data, separators = (',', ':'))
    return raw_value.replace('\\', '\\\\').replace('"', '\\"')

def _analyze_game(engine: chess.engine.SimpleEngine, game: chess.pgn.Game,
                  depth_limit: int) -> None:
    """ Add a White-relative rating series to one PGN game's custom header. """

    if (len(game.errors) > 0):
        raise ValueError(f'PGN parser errors: {game.errors[0]}')

    if ((game.headers.get('Event') == '?')
            and (game.headers.get('Site') == '?')
            and (game.headers.get('Date') == '????.??.??')
            and (len(list(game.mainline_moves())) == 0)):
        raise ValueError('No PGN game found.')

    board = game.board()
    ratings = [get_rating_value(engine, board, depth_limit)]
    for move in game.mainline_moves():
        board.push(move)
        ratings.append(get_rating_value(engine, board, depth_limit))

    game.headers[ANNOTATION_HEADER] = _annotation(ratings, depth_limit)

def _write_atomically(path: str, contents: str) -> None:
    """ Replace a file only after all its replacement contents have been written. """

    directory = os.path.dirname(path)
    descriptor, temporary_path = tempfile.mkstemp(prefix = '.stockfish-analysis-',
            dir = directory, text = True)

    try:
        with os.fdopen(descriptor, 'w', encoding = 'utf-8') as file:
            file.write(contents)

        os.replace(temporary_path, path)
    except Exception:
        if (os.path.exists(temporary_path)):
            os.unlink(temporary_path)
        raise

def _analyze_pgn_file(engine: chess.engine.SimpleEngine,
        path: str, depth_limit: int) -> int:
    """ Analyze every game in one PGN file and return its game count. """

    games: list[chess.pgn.Game] = []
    with open(path, 'r', encoding = 'utf-8') as file:
        while True:
            game = chess.pgn.read_game(file)
            if (game is None):
                break

            _analyze_game(engine, game, depth_limit)
            games.append(game)

    if (len(games) == 0):
        raise ValueError('No PGN games found.')

    contents = '\n\n'.join(str(game).strip() for game in games) + '\n'
    _write_atomically(path, contents)

    return len(games)

def _find_pgn_files() -> list[str]:
    """ Return mounted PGN files in deterministic recursive path order. """

    paths: list[str] = []
    for root, directories, filenames in os.walk(GAMES_DIR):
        directories.sort()
        for filename in sorted(filenames):
            if (filename.lower().endswith('.pgn')):
                paths.append(os.path.join(root, filename))

    return paths

def analyze_batch(engine: chess.engine.SimpleEngine, depth_limit: int,
        allow_empty: bool) -> tuple[dict[str, typing.Any], int]:
    """ Analyze mounted PGNs, returning a JSON summary and process exit code. """

    paths = _find_pgn_files()
    summary: dict[str, typing.Any] = {
        'mode': 'batch',
        'config': {'depth': depth_limit},
        'files_discovered': len(paths),
        'files_succeeded': 0,
        'files_failed': 0,
        'files': [],
    }

    if ((len(paths) == 0) and (not allow_empty)):
        summary['error'] = f'No .pgn files found under {GAMES_DIR!r}. Mount games there or pass --allow-empty.'
        return (summary, 1)

    for path in paths:
        relative_path = os.path.relpath(path, GAMES_DIR)
        try:
            game_count = _analyze_pgn_file(engine, path, depth_limit)
            summary['files_succeeded'] += 1
            summary['files'].append({'path': relative_path, 'status': 'success',
                                     'games_analyzed': game_count})
        except (OSError, ValueError, chess.engine.EngineError) as error:
            summary['files_failed'] += 1
            summary['files'].append({'path': relative_path, 'status': 'failed',
                                     'error': str(error)})

    exit_code = 0
    if (summary['files_failed'] > 0):
        exit_code = 1

    return (summary, exit_code)

def _get_parser() -> argparse.ArgumentParser:
    """ Construct the command-line parser for both analysis modes. """

    parser = argparse.ArgumentParser(
        description = 'Analyze a FEN or all mounted PGNs using the bundled Stockfish engine.')

    parser.add_argument('fen', nargs = '?', default = None,
                        help = 'FEN to analyze. Omit to analyze all PGNs under /games.')

    parser.add_argument('moves', nargs = '?', default = None,
                        help = 'Optional comma-separated SAN moves to play after FEN analysis.')

    parser.add_argument('--depth', type = int, default = DEFAULT_DEPTH_LIMIT,
                        help = 'Stockfish search depth per position (default: %(default)s).')

    parser.add_argument('--allow-empty', action = 'store_true',
                        help = 'Allow batch mode to succeed when /games contains no PGNs.')

    return parser

def display_output(output: str) -> None:
    print(json.dumps(output, indent = 4))

def display_error(error_message: str) -> None:
    print(json.dumps({'error': error_message}))

def main() -> None:
    """ Run the requested FEN or mounted-PGN analysis mode. """

    args = _get_parser().parse_args()
    if (args.depth < 1):
        display_error('--depth must be at least 1.')
        return 2

    try:
        engine = init_engine()

        output = None
        exit_code = 0
        try:
            if (args.fen is not None):
                output = analyze_fen(engine, args.fen, args.moves, args.depth)
            else:
                (output, exit_code) = analyze_batch(engine, args.depth, args.allow_empty)
        except Exception as ex:
            display_error(str(ex))
            return 1

        display_output(output)
        return exit_code
    except Exception:
        display_error('Failed to initialize engine.')
        return 1
    finally:
        engine.quit()

    return exit_code

if (__name__ == '__main__'):
    sys.exit(main())
