from operator import add, mul

def product(n, term):
    result = 1
    k = 1
    while k <= n:
        result *= term(k)
        k += 1
    return result

def accumulate(merger, base, n, term):
    result = base
    for k in range(1, n + 1):
        result = merger(result, term(k))
    return result

def summation_using_accumulate(n, term):
    return sum(term(k) for k in range(1, n + 1))

def product_using_accumulate(n, term):
    return product(n, term)
