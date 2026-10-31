def largest_factor(n):
    for divisor in range(2, n + 1):
        if n % divisor == 0:
            return n // divisor

def hailstone(n):
    sequence = [n]
    while sequence[-1] != 1:
        value = sequence[-1]
        sequence.append(value // 2 if value % 2 == 0 else 3 * value + 1)
    for value in sequence:
        print(value)
    return len(sequence)
