def largest_factor(n):
    for candidate in range(2, n):
        if n % candidate == 0:
            return candidate
    return 1

def hailstone(n):
    print(n)
    if n == 1:
        return 1
    following = n // 2 if n % 2 == 0 else 3 * n + 1
    return 1 + hailstone(following)
