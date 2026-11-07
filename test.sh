#!/bin/bash

# test a few basic test cases for the stockfish container

readonly THIS_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd | xargs realpath)"
readonly ROOT_DIR="${THIS_DIR}/.."

readonly IMAGE_NAME="${IMAGE_NAME:-stockfish-engine}"
readonly CONTAINER_USER="$(id -u):$(id -g)"

readonly START_FEN="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
readonly MATE_FEN="r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5Q2/PPPP1PPP/RNB1K2R w KQkq - 4 4"
readonly MATE_MOVE="Qxf7"

function test_standard_opening() {
    echo -n "Standard Opening ... "
    local output
    output=$(docker run --rm "$IMAGE_NAME" "$START_FEN" "e4, e5" --depth 10 2>/dev/null) || true
    
    if echo "$output" | grep -q '"initial_rating"' \
            && echo "$output" | grep -q '"analysis_steps"' \
            && echo "$output" | grep -q '"final_rating"'; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function test_fen_only() {
    echo -n "FEN Only ... "
    local output
    output=$(docker run --rm "$IMAGE_NAME" "$START_FEN" --depth 6 2>/dev/null) || true

    if echo "$output" | grep -q '"initial_rating"' && echo "$output" | grep -q '"analysis_steps": \[\]'; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function test_checkmate_detection() {
    echo -n "Checkmate Detection ... "
    local mate_output
    mate_output=$(docker run --rm "$IMAGE_NAME" "$MATE_FEN" "$MATE_MOVE" --depth 10 2>/dev/null) || true
    
    if echo "$mate_output" | grep -q "\"rating\": \"M0\""; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function test_invalid_move_handling() {
    echo -n "Invalid Move Handling ... "
    local error_output
    error_output=$(docker run --rm "$IMAGE_NAME" "$START_FEN" "NotAMove" --depth 5 2>/dev/null) || true
    
    if echo "$error_output" | grep -q "Invalid move"; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function test_batch_annotation() {
    echo -n "Batch PGN Annotation ... "
    local temp_dir
    temp_dir=$(mktemp -d) || return 1
    trap 'rm -rf "$temp_dir"' RETURN
    mkdir -p "$temp_dir/nested"

    printf '%s\n' \
        '[Event "Test"]' '[Site "Local"]' '[Date "2026.01.01"]' '[Round "1"]' \
        '[White "White"]' '[Black "Black"]' '[Result "*"]' '' \
        '1. e4 e5 2. Nf3 *' > "$temp_dir/nested/game.pgn"

    local output
    output=$(docker run --rm --user "$CONTAINER_USER" -v "$temp_dir:/games" "$IMAGE_NAME" --depth 6 2>/dev/null) || true
    if echo "$output" | grep -q '"files_succeeded": 1' \
            && grep -q 'ChessAIStockfishAnalysis' "$temp_dir/nested/game.pgn" \
            && grep -q 'score_perspective' "$temp_dir/nested/game.pgn" \
            && grep -q '"ply"' "$temp_dir/nested/game.pgn" \
            && ! grep -q 'description' "$temp_dir/nested/game.pgn"; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function test_batch_failure_continues() {
    echo -n "Batch Failure Handling ... "
    local temp_dir
    temp_dir=$(mktemp -d) || return 1
    trap 'rm -rf "$temp_dir"' RETURN

    printf '%s\n' \
        '[Event "Test"]' '[Site "Local"]' '[Date "2026.01.01"]' '[Round "1"]' \
        '[White "White"]' '[Black "Black"]' '[Result "*"]' '' \
        '1. d4 d5 *' > "$temp_dir/good.pgn"
    printf '%s\n' 'not a PGN' > "$temp_dir/bad.pgn"

    local output
    docker run --rm --user "$CONTAINER_USER" -v "$temp_dir:/games" "$IMAGE_NAME" --depth 6 > "$temp_dir/output.json" 2>/dev/null
    local status=$?
    output=$(<"$temp_dir/output.json")
    if [[ $status -ne 0 ]] && echo "$output" | grep -q '"files_failed": 1' \
            && grep -q 'ChessAIStockfishAnalysis' "$temp_dir/good.pgn"; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function test_empty_batch() {
    echo -n "Empty Batch ... "
    local temp_dir
    temp_dir=$(mktemp -d) || return 1
    trap 'rm -rf "$temp_dir"' RETURN

    docker run --rm --user "$CONTAINER_USER" -v "$temp_dir:/games" "$IMAGE_NAME" > /dev/null 2>&1
    local default_status=$?
    docker run --rm --user "$CONTAINER_USER" -v "$temp_dir:/games" "$IMAGE_NAME" --allow-empty > /dev/null 2>&1
    local allowed_status=$?

    if [[ $default_status -ne 0 && $allowed_status -eq 0 ]]; then
        echo "ok"
        return 0
    else
        echo "FAILED"
        return 1
    fi
}

function main() {
    if [[ $# -ne 0 ]]; then
        echo "USAGE: $0"
        exit 1
    fi

    trap exit SIGINT

    cd "${ROOT_DIR}"

    echo "Starting Chess-Analyzer Integration Tests..."
    echo "--------------------------------------------"

    local error_count=0

    test_standard_opening || ((error_count++))
    test_fen_only || ((error_count++))
    test_checkmate_detection || ((error_count++))
    test_invalid_move_handling || ((error_count++))
    test_batch_annotation || ((error_count++))
    test_batch_failure_continues || ((error_count++))
    test_empty_batch || ((error_count++))

    echo "--------------------------------------------"
    if [[ ${error_count} -eq 0 ]]; then
        echo "All integration tests passed successfully!"
    else
        echo "Completed with failures. Total tests failed: ${error_count}"
    fi

    return ${error_count}
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
