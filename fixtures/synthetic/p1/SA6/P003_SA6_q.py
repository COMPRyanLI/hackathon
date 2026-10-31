# Author: [NAME]
# Purpose: SYNTHETIC N-queens style demo

N = 6

def safe(board, row, col):
    for r in range(row):
        if board[r][col] == 1:
            return False
    for r in range(row):
        for c in range(N):
            if board[r][c] == 1 and abs(r - row) == abs(c - col):
                return False
    return True


def solve(board, row):
    if row == N:
        return True
    for col in range(N):
        if safe(board, row, col):
            board[row][col] = 1
            if solve(board, row + 1):
                return True
            board[row][col] = 0
    return False


board = [[0] * N for _ in range(N)]
print(solve(board, 0))
