"""
This script is the entry point for a Docker container with the Stockfish engine.
It will take the FEN of a board and moves and output the rating for the board as it performs each of the moves from the moveline.

If a FEN is not provided, the /games directory will be searched for PGNs to annotate in-place.
"""

import argparse
import json
import logging
import os
import re
import stat
import sys
import tempfile
import typing

import chess
import chess.engine
import chess.pgn
import chessai.core.gameannotation

DEFAULT_DEPTH_LIMIT = 15
GAMES_DIR = '/games'
STOCKFISH_PATH = '/usr/bin/stockfish'

GAME_RATING_ANNOTATION_HEADER: str = 'ChessAIStockfishAnalysis'

RatingScore: typing.TypeAlias = float
MateScore: typing.TypeAlias = str
MoveError: typing.TypeAlias = str
Rating: typing.TypeAlias = RatingScore | MateScore | MoveError

class AnalysisConfig(typing.TypedDict):
    """ The Stockfish settings used for one analysis request. """

    depth: int

class FenAnalysis(typing.TypedDict):
    """ The JSON response for a FEN analysis request. """

    initial_fen: str
    config: AnalysisConfig
    initial_rating: Rating
    analysis_steps: list[Rating]
    final_rating: Rating

class BatchFileResult(typing.TypedDict):
    """ The common fields in a result for one PGN file. """

    path: str
    status: str

class BatchFileSuccess(BatchFileResult):
    """ A PGN file that was completely annotated and rewritten. """

    games_analyzed: int

class BatchFileFailure(BatchFileResult):
    """ A PGN file that could not be annotated without changing it. """

    error: str

BatchFileOutcome: typing.TypeAlias = BatchFileSuccess | BatchFileFailure

class BatchAnalysis(typing.TypedDict):
    """ The JSON response for a batch PGN analysis request. """

    mode: str
    config: AnalysisConfig
    files_discovered: int
    files_succeeded: int
    files_failed: int
    files: list[BatchFileOutcome]
    error: typing.NotRequired[str]

def init_engine() -> chess.engine.SimpleEngine:
    """ Start the Stockfish binary installed in this image. """

    return chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)

def get_rating_value(engine: chess.engine.SimpleEngine, board: chess.Board,
                     depth_limit: int) -> Rating:
    """
    Return the evaluation from White's point of view, in pawns
    (e.g. 1.66 = White is ahead by about 1.66 pawns), or a mate string
    such as 'M3' (White mates in 3) / '-M3' (Black mates in 3).
    """

    # The game is already over, so the engine has nothing useful to say.
    if (board.is_checkmate()):
        if (board.turn == chess.BLACK):
            return 'M0'

        return '-M0'

    info = engine.analyse(board, chess.engine.Limit(depth = depth_limit))
    score = info['score'].white()

    mate = score.mate()
    if (mate is not None):
        if (mate > 0):
            return f"M{mate}"

        return f"-M{abs(mate)}"

    centipawns = score.score()
    if (centipawns is None):
        logging.error('Stockfish returned a score without a centipawn value.')

    return round(centipawns / 100, 2)

def get_moveline_ratings(
        engine: chess.engine.SimpleEngine,
        board: chess.Board,
        depth_limit: int, moves: str | None
        ) -> list[Rating]:
    """
    Parses a comma-separated string, updates the board position, and returns a move-by-move
    log of rating values.
    """

    if (moves is None):
        return []

    steps_history: list[MoveAnalysisStep | MoveAnalysisError] = []

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
            steps_history.append(f"Invalid move: {move_san}")
            break

        # Update the board with the next move.
        board.push(verified_move)

        # Add the rating for the board after the current move.
        step_evaluation = get_rating_value(engine, board, depth_limit)
        steps_history.append(step_evaluation)

    return steps_history

def analyze_fen(engine: chess.engine.SimpleEngine, fen: str, moves: str | None,
                depth_limit: int) -> (FenAnalysis | None, int):
    """ Analyze one FEN and its optional comma-separated SAN move line. """

    try:
        board = chess.Board(fen)
    except ValueError as error:
        logging.exception(f"Invalid FEN string provided: {fen!r}", exc_info = error)
        return None, 1

    output: FenAnalysis = {
        'initial_fen': fen,
        'config': {'depth': depth_limit},
        'initial_rating': 0.0,
        'analysis_steps': [],
        'final_rating': 0.0,
    }

    output['initial_rating'] = get_rating_value(engine, board, depth_limit)

    move_ratings = get_moveline_ratings(engine, board, depth_limit, moves)
    output['analysis_steps'] = move_ratings

    if ((len(move_ratings) > 0) and ('rating' in move_ratings[-1])):
        final_step = typing.cast(MoveAnalysisStep, move_ratings[-1])
        output['final_rating'] = final_step['rating']
    else:
        output['final_rating'] = output['initial_rating']

    return output, 0

def _annotation(ratings: list[Rating], depth_limit: int) -> str:
    """ Build the compact JSON stored in an analyzed PGN's custom header. """

    annotation = {
        'engine': {
            'name': 'Stockfish',
            'commit': os.environ.get('STOCKFISH_COMMIT', 'unknown'),
        },
        'depth': depth_limit,
        'annotations': ratings,
    }

    return json.dumps(annotation, separators = (',', ':'))

def _analyze_game(engine: chess.engine.SimpleEngine, game: chess.pgn.Game,
                  depth_limit: int) -> int:
    """ Add a White-relative rating series to one PGN game's custom header. """

    if (len(game.errors) > 0):
        logging.error(f"PGN parser errors: {game.errors[0]}.")

    if ((game.headers.get('Event') == '?')
            and (game.headers.get('Site') == '?')
            and (game.headers.get('Date') == '????.??.??')
            and (len(list(game.mainline_moves())) == 0)):
        logging.error('No PGN game found.')

    board = game.board()
    ratings = [get_rating_value(engine, board, depth_limit)]
    moves = list(game.mainline_moves())
    for move in moves:
        board.push(move)
        ratings.append(get_rating_value(engine, board, depth_limit))

    header = GAME_RATING_ANNOTATION_HEADER
    game.headers[header] = _annotation(ratings, depth_limit)

def _write_atomically(path: str, contents: str) -> int:
    """ Replace a file after a durable write while retaining its permissions. """

    directory = os.path.dirname(path)
    descriptor, temporary_path = tempfile.mkstemp(prefix = '.stockfish-analysis-',
            dir = directory, text = True)

    try:
        with os.fdopen(descriptor, 'w', encoding = 'utf-8') as file:
            permissions = stat.S_IMODE(os.stat(path).st_mode)
            os.fchmod(file.fileno(), permissions)
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception as error:
        if (os.path.exists(temporary_path)):
            os.unlink(temporary_path)
        logging.exception('Failed to write file.', exc_info = error)
        return 1

    return 0

def _analyze_pgn_file(engine: chess.engine.SimpleEngine,
        path: str, depth_limit: int) -> tuple[int, int]:
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
        logging.error('No PGN games found.')
        return 0, 1

    contents = '\n\n'.join(str(game).strip() for game in games) + '\n'
    status = _write_atomically(path, contents)
    if (status != 0):
        return 0, 1

    return len(games), 0

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
        allow_empty: bool) -> tuple[BatchAnalysis, int]:
    """ Analyze mounted PGNs, returning a JSON summary and process exit code. """

    paths = _find_pgn_files()
    summary: BatchAnalysis = {
        'mode': 'batch',
        'config': {'depth': depth_limit},
        'files_discovered': len(paths),
        'files_succeeded': 0,
        'files_failed': 0,
        'files': [],
    }

    if ((len(paths) == 0) and (not allow_empty)):
        summary['error'] = f"No .pgn files found under {GAMES_DIR!r}. Mount games there or pass --allow-empty."
        return (summary, 1)

    for path in paths:
        relative_path = os.path.relpath(path, GAMES_DIR)
        game_count, status = _analyze_pgn_file(engine, path, depth_limit)
        if (status != 0):
            summary['files_failed'] += 1
            failed_file: BatchFileFailure = {
                'path': relative_path,
                'status': 'failed',
                'error': str(error),
            }
            summary['files'].append(failed_file)
        else:
            summary['files_succeeded'] += 1
            successful_file: BatchFileSuccess = {
                'path': relative_path,
                'status': 'success',
                'games_analyzed': game_count,
            }
            summary['files'].append(successful_file)

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

def display_output(output: FenAnalysis | BatchAnalysis) -> None:
    print(json.dumps(output, sort_keys = True, indent = 4))

def main() -> int:
    """ Run the requested FEN or mounted-PGN analysis mode. """

    args = _get_parser().parse_args()
    if (args.depth < 1):
        logging.error('--depth must be at least 1.')
        return 2

    engine: chess.engine.SimpleEngine | None = None
    try:
        engine = init_engine()
    except Exception as error:
        logging.exception('Failed to initialize engine.', exc_info = error)

        if (engine is not None):
            engine.quit()

        return 1

    output: FenAnalysis | BatchAnalysis
    try:
        if (args.fen is not None):
            (output, exit_code) = analyze_fen(engine, args.fen, args.moves, args.depth)
        else:
            (output, exit_code) = analyze_batch(engine, args.depth, args.allow_empty)
    except Exception as error:
        logging.exception('Failed to analyze moves.', exc_info = error)

        if (engine is not None):
            engine.quit()

        return 1

    if (engine is not None):
        engine.quit()

    if (output is not None):
        display_output(output)

    return exit_code

if (__name__ == '__main__'):
    sys.exit(main())
