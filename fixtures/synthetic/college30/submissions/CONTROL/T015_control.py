def largest_factor(n):
    answer = 1
    for divisor in range(2, int(n ** 0.5) + 1):
        if n % divisor == 0:
            answer = divisor
    return answer

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
