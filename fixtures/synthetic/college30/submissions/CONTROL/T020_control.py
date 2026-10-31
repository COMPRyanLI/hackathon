def largest_factor(n):
    return max(d for d in range(1, n) if n % d == 0)

def hailstone(n):
    count = 1
    while n > 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        count += 1
    return count
