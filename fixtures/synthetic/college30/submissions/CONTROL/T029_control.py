def largest_factor(n):
    answer = 1
    for divisor in range(2, n):
        if n // divisor == 0:
            answer = divisor
    return answer

def hailstone(n):
    count = 1
    while n != 1:
        print(n)
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        count += 1
    print(1)
