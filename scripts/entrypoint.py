"""
This script is the entry point for a Docker container with the Stockfish engine.
It will take the FEN of a board and moves and output the rating for the board as it performs each of the moves from the moveline.

If a FEN is not provided, the /games directory will be searched for PGNs to annotate in-place.
"""

import argparse
import json
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
        raise chess.engine.EngineError('Stockfish returned a score without a centipawn value.')

    return round(centipawns / 100, 2)

def describe_rating(rating: Rating) -> str:
    """ Turn a rating into a plain-English description. """

    if (isinstance(rating, str)):
        if rating.startswith('-'):
            winner = 'Black'
        else:
            winner = 'White'

        description = f"{winner} has a forced checkmate"
        numbers = re.findall(r"\d+", rating)
        if (len(numbers) > 0):
            description += f" in {numbers[0]} moves"

        return description

    size = abs(rating)
    if (size < 0.30):
        return 'The position is about equal'

    if (rating > 0):
        side = 'White'
    else:
        side = 'Black'

    if (size < 0.80):
        return f"{side} is slightly better"

    if (size < 1.50):
        return f"{side} is clearly better"

    if (size < 3.00):
        return f"{side} has a decisive advantage"

    return f"{side} is winning"

def get_moveline_ratings(
        engine: chess.engine.SimpleEngine,
        board: chess.Board,
        depth_limit: int, moves: str | None
        ) -> list[MoveAnalysisStep | MoveAnalysisError]:
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
            "rating": step_evaluation,
            "description": describe_rating(step_evaluation)
        })

    return steps_history

def analyze_fen(engine: chess.engine.SimpleEngine, fen: str, moves: str | None,
                depth_limit: int) -> FenAnalysis:
    """ Analyze one FEN and its optional comma-separated SAN move line. """

    try:
        board = chess.Board(fen)
    except ValueError as error:
        raise ValueError(f"Invalid FEN string provided: {fen!r}") from error

    output: FenAnalysis = {
        'initial_fen': fen,
        'config': {'depth': depth_limit},
        'initial_rating': 0.0,
        'initial_description': '',
        'analysis_steps': [],
        'final_rating': 0.0,
        'final_description': '',
    }

    output['initial_rating'] = get_rating_value(engine, board, depth_limit)
    output['initial_description'] = describe_rating(output['initial_rating'])

    move_ratings = get_moveline_ratings(engine, board, depth_limit, moves)
    output['analysis_steps'] = move_ratings

    if ((len(move_ratings) > 0) and ('rating' in move_ratings[-1])):
        final_step = typing.cast(MoveAnalysisStep, move_ratings[-1])
        output['final_rating'] = final_step['rating']
    else:
        output['final_rating'] = output['initial_rating']

    output['final_description'] = describe_rating(output['final_rating'])

    return output

def _annotation(ratings: list[Rating], moves: list[chess.Move], depth_limit: int) -> str:
    """ Build the compact JSON stored in an analyzed PGN's custom header. """

    if (len(ratings) != len(moves) + 1):
        raise ValueError('Game annotations require one rating for the initial position and each move.')

    annotations: list[chessai.core.gameannotation.MoveRatingAnnotation] = []
    for ply, rating in enumerate(ratings):
        move: str | None = None
        if (ply > 0):
            move = moves[ply - 1].uci()

        annotations.append(chessai.core.gameannotation.MoveRatingAnnotation(
            ply, rating, move, describe_rating(rating)))

    annotation = chessai.core.gameannotation.GameRatingAnnotation(
        engine = chessai.core.gameannotation.RatingEngine(
            'Stockfish', os.environ.get('STOCKFISH_COMMIT', 'unknown')),
        depth = depth_limit,
        annotations = annotations,
    )
    return annotation.to_header_value()

def _analyze_game(engine: chess.engine.SimpleEngine, game: chess.pgn.Game,
                  depth_limit: int) -> None:
    """ Add a White-relative rating series to one PGN game's custom header. """

    if (len(game.errors) > 0):
        raise ValueError(f"PGN parser errors: {game.errors[0]}")

    if ((game.headers.get('Event') == '?')
            and (game.headers.get('Site') == '?')
            and (game.headers.get('Date') == '????.??.??')
            and (len(list(game.mainline_moves())) == 0)):
        raise ValueError('No PGN game found.')

    board = game.board()
    ratings = [get_rating_value(engine, board, depth_limit)]
    moves = list(game.mainline_moves())
    for move in moves:
        board.push(move)
        ratings.append(get_rating_value(engine, board, depth_limit))

    header = chessai.core.gameannotation.GAME_RATING_ANNOTATION_HEADER
    game.headers[header] = _annotation(ratings, moves, depth_limit)

def _write_atomically(path: str, contents: str) -> None:
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
        try:
            game_count = _analyze_pgn_file(engine, path, depth_limit)
            summary['files_succeeded'] += 1
            successful_file: BatchFileSuccess = {
                'path': relative_path,
                'status': 'success',
                'games_analyzed': game_count,
            }
            summary['files'].append(successful_file)
        except (OSError, ValueError, chess.engine.EngineError) as error:
            summary['files_failed'] += 1
            failed_file: BatchFileFailure = {
                'path': relative_path,
                'status': 'failed',
                'error': str(error),
            }
            summary['files'].append(failed_file)

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
    print(json.dumps(output, indent = 4))

def display_error(error_message: str) -> None:
    print(json.dumps({'error': error_message}))

def main() -> int:
    """ Run the requested FEN or mounted-PGN analysis mode. """

    args = _get_parser().parse_args()
    if (args.depth < 1):
        display_error('--depth must be at least 1.')
        return 2

    engine: chess.engine.SimpleEngine | None = None
    try:
        engine = init_engine()

        output: FenAnalysis | BatchAnalysis
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
        if (engine is not None):
            engine.quit()

    return exit_code

if (__name__ == '__main__'):
    sys.exit(main())
