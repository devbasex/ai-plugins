def average(values):
    if not values:
        raise ValueError("average() of empty sequence is undefined")
    total = 0
    for v in values:
        total += v
    return total / len(values)
