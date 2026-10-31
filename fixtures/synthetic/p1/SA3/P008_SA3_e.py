# Author: [NAME]
# Date: 2026-09-29
# Purpose: SYNTHETIC demo program

RATE = 1.05
INITIAL_BALANCE = 100.00
END_YEAR = 20

balance = INITIAL_BALANCE * RATE ** END_YEAR

print("Final balance:", balance)
print("Interest earned:", balance - INITIAL_BALANCE)
