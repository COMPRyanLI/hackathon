def largest_factor(n):
    return max(d for d in range(1, n) if n % d == 0)

def hailstone(n):
    count = 0
    print(n)
    while n > 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        print(n)
        count += 1
    return count
