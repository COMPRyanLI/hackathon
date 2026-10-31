def largest_factor(n):
    candidate = n - 1
    while candidate > 0:
        if n % candidate == 0:
            return candidate
        candidate -= 1

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
