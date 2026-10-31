def largest_factor(n):
    candidate = n - 1
    while candidate > 0:
        if n % candidate == 0:
            return candidate
        candidate -= 1

def hailstone(n):
    print(n)
    if n == 1:
        return 1
    following = n // 2 if n % 2 == 0 else 3 * n + 1
    return 1 + hailstone(following)
