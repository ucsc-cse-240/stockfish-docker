#!/bin/bash

# test a few basic test cases for the stockfish container

readonly THIS_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd | xargs realpath)"
readonly ROOT_DIR="${THIS_DIR}/.."

readonly START_FEN="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
readonly MATE_FEN="r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5Q2/PPPP1PPP/RNB1K2R w KQkq - 4 4"
readonly MATE_MOVE="Qxf7"

function test_standard_opening() {
    echo -n "Standard Opening ... "
    local output
    output=$(docker run --rm stockfish-analyzer "$START_FEN" "e4, e5" --depth 10 2>/dev/null) || true
    
    if echo "$output" | grep -q "\"final_rating\""; then
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
    mate_output=$(docker run --rm stockfish-analyzer "$MATE_FEN" "$MATE_MOVE" --depth 10 2>/dev/null) || true
    
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
    error_output=$(docker run --rm stockfish-analyzer "$START_FEN" "NotAMove" --depth 5 2>/dev/null) || true
    
    if echo "$error_output" | grep -q "Invalid move"; then
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
    test_checkmate_detection || ((error_count++))
    test_invalid_move_handling || ((error_count++))

    echo "--------------------------------------------"
    if [[ ${error_count} -eq 0 ]]; then
        echo "All integration tests passed successfully!"
    else
        echo "Completed with failures. Total tests failed: ${error_count}"
    fi

    return ${error_count}
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"