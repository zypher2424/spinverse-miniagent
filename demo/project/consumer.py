from arithmetic import add


def total(values):
    value = 0
    for item in values:
        value = add(value, item)
    return value
