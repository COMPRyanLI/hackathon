def largest_factor(n):
    answer = 1
    for candidate in range(2, n):
        if n % candidate == 0:
            answer = candidate
    return answer

def hailstone(n):
    count = 0
    print(n)
    while n > 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        print(n)
        count += 1
    return count
