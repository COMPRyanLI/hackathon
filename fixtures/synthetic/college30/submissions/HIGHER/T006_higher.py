from operator import add, mul

def product(n, term):
    result = 0
    for k in range(1, n + 1):
        result *= term(k)
    return result

def accumulate(merger, base, n, term):
    k = 1
    while k <= n:
        base = merger(base, term(k))
        k += 1
    return base

def summation_using_accumulate(n, term):
    return accumulate(add, 0, n, term)

def product_using_accumulate(n, term):
    return accumulate(mul, 1, n, term)
