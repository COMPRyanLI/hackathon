from operator import add, mul

def product(n, term):
    answer = 1
    for index in range(1, n + 1):
        answer = answer * term(index)
    return answer

def accumulate(merger, base, n, term):
    k = 1
    while k <= n:
        base = merger(base, term(k))
        k += 1
    return base

def summation_using_accumulate(n, term):
    result = accumulate(add, 0, n, term)
    return result

def product_using_accumulate(n, term):
    result = accumulate(mul, 1, n, term)
    return result
