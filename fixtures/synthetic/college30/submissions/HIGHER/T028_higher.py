from operator import add, mul

def product(n, term):
    result = 0
    for k in range(1, n + 1):
        result *= term(k)
    return result

def accumulate(merger, base, n, term):
    result = 0
    for k in range(1, n + 1):
        result = merger(result, term(k))
    return result

def summation_using_accumulate(n, term):
    return accumulate(add, 0, n, term)

def product_using_accumulate(n, term):
    return accumulate(mul, 0, n, term)
