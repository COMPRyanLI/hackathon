def largest_factor(n):
    for divisor in range(n // 2, 0, -1):
        if n % divisor == 0:
            return divisor

def hailstone(n):
    if n == 1:
        print(1)
        return 0
    count = 1
    print(n)
    while n > 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        print(n)
        count += 1
    return count
