"""Original demo reference implementation; not an official university answer key."""
from operator import add, mul


def product(n, term):
    value = 1
    for index in range(1, n + 1):
        value *= term(index)
    return value


def accumulate(merger, base, n, term):
    value = base
    for index in range(1, n + 1):
        value = merger(value, term(index))
    return value


def summation_using_accumulate(n, term):
    return accumulate(add, 0, n, term)


def product_using_accumulate(n, term):
    return accumulate(mul, 1, n, term)
