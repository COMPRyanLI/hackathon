from operator import add, mul

def product(n, term):
    if n == 0:
        return 1
    return product(n - 1, term) * term(n)

def accumulate(merger, base, n, term):
    if n == 0:
        return base
    return merger(accumulate(merger, base, n - 1, term), term(n))

def summation_using_accumulate(n, term):
    return accumulate(add, 0, n, term)

def product_using_accumulate(n, term):
    return accumulate(mul, 1, n, term)
