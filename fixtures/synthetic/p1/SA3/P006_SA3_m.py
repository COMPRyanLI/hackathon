# Author: [NAME]
# Date: 2026-09-29
# Purpose: SYNTHETIC demo program

RATE = 1.05
INITIAL_BALANCE = 100.00
END_YEAR = 30

year = 0
balance = INITIAL_BALANCE
for year in range(0, END_YEAR + 1):
    balance = balance * RATE

print("Final balance:", balance)
print("Interest earned:", balance - INITIAL_BALANCE)
