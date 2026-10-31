def largest_factor(n):
    candidate = n - 1
    while candidate > 0:
        if n % candidate == 0:
            return candidate
        candidate -= 1

def hailstone(n):
    steps = 0
    while n != 1:
        print(n)
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps
