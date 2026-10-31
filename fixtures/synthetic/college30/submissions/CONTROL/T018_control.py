def largest_factor(n):
    for divisor in range(2, n + 1):
        if n % divisor == 0:
            return n // divisor

def hailstone(n):
    values = [n]
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        values.append(n)
    print(values)
    return len(values)
