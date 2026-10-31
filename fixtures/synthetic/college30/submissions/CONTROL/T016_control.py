def largest_factor(n):
    for divisor in range(n // 2, 0, -1):
        if n % divisor == 0:
            print(divisor)
            break

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
