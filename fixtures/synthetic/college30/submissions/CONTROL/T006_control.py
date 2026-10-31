def largest_factor(n):
    for divisor in range(2, n + 1):
        if n % divisor == 0:
            return n // divisor

def hailstone(n):
    length = 1
    print(n)
    while n != 1:
        if n % 2 == 0:
            n = n / 2
        else:
            n = 3 * n + 1
        print(n)
        length += 1
    return length
