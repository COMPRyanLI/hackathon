from operator import add, mul

def product(n, term):
    answer = 1
    for index in range(1, n + 1):
        answer *= term(index)
        return answer

def accumulate(merger, base, n, term):
    result = base
    for k in range(1, n + 1):
        result = merger(base, term(k))
    return result

def summation_using_accumulate(n, term):
    return sum(term(k) for k in range(1, n + 1))

def product_using_accumulate(n, term):
    return product(n, term)
