"""Original demo reference implementation; not an official university answer key."""


def largest_factor(n):
    for divisor in range(2, n + 1):
        if n % divisor == 0:
            return n // divisor


def hailstone(n):
    count = 0
    while True:
        print(n)
        count += 1
        if n == 1:
            return count
        n = n // 2 if n % 2 == 0 else 3 * n + 1
