"""Generate the SYNTHETIC P1 fixture sources (run once; output is committed fixture data)."""
import sys
from pathlib import Path

root = Path(sys.argv[1])
HDR = "# Author: [NAME]\n# Date: 2026-09-29\n# Purpose: SYNTHETIC demo program\n\n"


def p1(loop: str, rate="1.05", start="0", end="20", name_rate="RATE", name_bal="INITIAL_BALANCE", pow_line=False):
    body = (f"{name_rate} = {rate}\n{name_bal} = 100.00\nEND_YEAR = {end}\n\n"
            f"year = {start}\nbalance = {name_bal}\n")
    if loop == "while_le":
        body += "while year <= END_YEAR:\n    balance = balance * " + name_rate + "\n    year = year + 1\n"
    elif loop == "while_lt":
        body += "while year < END_YEAR:\n    balance = balance * " + name_rate + "\n    year = year + 1\n"
    elif loop == "for_incl":
        body += "for year in range(0, END_YEAR + 1):\n    balance = balance * " + name_rate + "\n"
    elif loop == "for_excl":
        body += "for year in range(0, END_YEAR):\n    balance = balance * " + name_rate + "\n"
    if pow_line:
        body = (f"{name_rate} = {rate}\n{name_bal} = 100.00\nEND_YEAR = {end}\n\n"
                f"balance = {name_bal} * {name_rate} ** END_YEAR\n")
    body += f'\nprint("Final balance:", balance)\nprint("Interest earned:", balance - {name_bal})\n'
    return HDR + body


def p2(compare="<"):
    return HDR + (
        "A_RATE = 1.05\nB_RATE = 1.04\nA_START = 100.00\nB_START = 150.00\n\n"
        "a_balance = A_START\nb_balance = B_START\nyear = 0\n"
        f"while a_balance {compare} b_balance:\n"
        "    a_balance = a_balance * A_RATE\n    b_balance = b_balance * B_RATE\n    year = year + 1\n\n"
        'print("Account A passes account B in year", year)\n')


files = {
    "P001_SA3_one.py": p1("while_le"), "P001_SA3_two.py": p2(),
    "P002_SA3_alpha.py": p1("for_incl"), "P002_SA3_beta.py": p2(),
    "P003_SA3_x.py": p1("while_le", end="25"), "P003_SA3_y.py": p2("<="),
    "P004_SA3_first.py": p1("while_le", rate="1.06"), "P004_SA3_second.py": p2(),
    "P005_SA3_a.py": p1("while_le", start="1"), "P005_SA3_b.py": p2(),
    "P006_SA3_m.py": p1("for_incl", end="30"), "P006_SA3_n.py": p2(),
    "P007_SA3_c.py": p1("while_lt"), "P007_SA3_d.py": p2(),
    "P008_SA3_e.py": p1("while_lt", pow_line=True), "P008_SA3_f.py": p2(),
    "P009_SA3_g.py": p1("for_excl", rate="0.05").replace("balance * RATE", "balance * (1 + RATE)"),
    "P009_SA3_h.py": p2(),
    "P010_SA3_combined.py": p1("while_lt") + "\n" + p2().replace(HDR, ""),
    "P011_SA3_i.py": p1("while_lt", name_rate="rate", name_bal="initial"), "P011_SA3_j.py": p2(),
}
(root / "SA3").mkdir(parents=True, exist_ok=True)
for name, text in files.items():
    (root / "SA3" / name).write_bytes(text.encode("utf-8"))

QHDR = "# Author: [NAME]\n# Purpose: SYNTHETIC N-queens style demo\n\nN = 6\n\n"
FULL = QHDR + '''def safe(cols, row, col):
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
            cols.pop()
    return False


cols = []
print(solve(cols, 0), cols)
'''
NO_DIAG = FULL.replace(" or abs(c - col) == abs(r - row)", "")
GRID = QHDR + '''def safe(board, row, col):
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
'''
NO_BACKTRACK = FULL.replace("            cols.pop()\n", "")
q = {"P001_SA6_q.py": FULL, "P002_SA6_q.py": NO_DIAG, "P003_SA6_q.py": GRID, "P004_SA6_q.py": NO_DIAG,
     "P005_SA6_q.py": FULL, "P006_SA6_q.py": NO_BACKTRACK, "P007_SA6_q.py": NO_DIAG.replace("N = 6", "N = 5"),
     "P008_SA6_q.py": GRID.replace("abs(r - row) == abs(c - col)", "r - row == c - col")}
(root / "SA6").mkdir(parents=True, exist_ok=True)
for name, text in q.items():
    (root / "SA6" / name).write_bytes(text.encode("utf-8"))
print(len(files), "SA3 files;", len(q), "SA6 files")
