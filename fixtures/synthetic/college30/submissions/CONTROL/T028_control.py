def largest_factor(n):
    return n // 2

def hailstone(n):
    steps = 0
    while n != 1:
        print(n)
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        steps += 1
    return steps
