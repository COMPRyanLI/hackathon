def largest_factor(n):
    return max(d for d in range(1, n) if n % d == 0)

def hailstone(n):
    print(n)
    if n == 1:
        return 1
    following = n // 2 if n % 2 == 0 else 3 * n + 1
    return 1 + hailstone(following)
