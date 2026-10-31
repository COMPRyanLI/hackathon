def largest_factor(n):
    answer = 1
    for candidate in range(2, n):
        if n % candidate == 0:
            answer = candidate
    return answer

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
