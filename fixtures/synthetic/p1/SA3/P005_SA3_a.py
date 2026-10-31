# Author: [NAME]
# Date: 2026-09-29
# Purpose: SYNTHETIC demo program

RATE = 1.05
INITIAL_BALANCE = 100.00
END_YEAR = 20

year = 1
balance = INITIAL_BALANCE
while year <= END_YEAR:
    balance = balance * RATE
    year = year + 1

print("Final balance:", balance)
print("Interest earned:", balance - INITIAL_BALANCE)
