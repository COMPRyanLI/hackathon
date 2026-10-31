from operator import add, mul

def product(n, term):
    from functools import reduce
    return reduce(mul, (term(k) for k in range(1, n + 1)), 1)

def accumulate(merger, base, n, term):
    result = base
    for k in range(1, n + 1):
        result = merger(result, term(k))
    return result

def summation_using_accumulate(n, term):
    return accumulate(add, 0, n, term)

def product_using_accumulate(n, term):
    return accumulate(mul, 1, n, term)
