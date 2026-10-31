def largest_factor(n):
    for candidate in range(2, n):
        if n % candidate == 0:
            return candidate
    return 1

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
