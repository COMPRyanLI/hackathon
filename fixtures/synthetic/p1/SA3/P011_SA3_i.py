# Author: [NAME]
# Date: 2026-09-29
# Purpose: SYNTHETIC demo program

rate = 1.05
initial = 100.00
END_YEAR = 20

year = 0
balance = initial
while year < END_YEAR:
    balance = balance * rate
    year = year + 1

print("Final balance:", balance)
print("Interest earned:", balance - initial)
