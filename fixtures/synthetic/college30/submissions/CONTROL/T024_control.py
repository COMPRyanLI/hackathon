def largest_factor(n):
    for candidate in range(n - 1, 1, -1):
        if n % candidate == 0:
            return candidate

def hailstone(n):
    count = 0
    print(n)
    while n > 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        print(n)
        count += 1
    return count
