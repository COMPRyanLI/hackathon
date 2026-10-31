# Author: [NAME]
# Date: 2026-09-29
# Purpose: SYNTHETIC demo program

A_RATE = 1.05
B_RATE = 1.04
A_START = 100.00
B_START = 150.00

a_balance = A_START
b_balance = B_START
year = 0
while a_balance < b_balance:
    a_balance = a_balance * A_RATE
    b_balance = b_balance * B_RATE
    year = year + 1

print("Account A passes account B in year", year)
