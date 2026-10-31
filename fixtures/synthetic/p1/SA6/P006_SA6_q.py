# Author: [NAME]
# Purpose: SYNTHETIC N-queens style demo

N = 6

def safe(cols, row, col):
    for r in range(row):
        c = cols[r]
        if c == col or abs(c - col) == abs(r - row):
            return False
    return True


def solve(cols, row):
    if row == N:
        return True
    for col in range(N):
        if safe(cols, row, col):
            cols.append(col)
            if solve(cols, row + 1):
                return True
    return False


cols = []
print(solve(cols, 0), cols)
