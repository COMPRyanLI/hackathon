def largest_factor(n):
    for candidate in range(n - 1, 1, -1):
        if n % candidate == 0:
            return candidate

def hailstone(n):
    length = 1
    print(n)
    while n != 1:
        if n % 2 == 0:
            n //= 2
        else:
            n = 3 * n + 1
        print(n)
        length += 1
    return length
