def validar_luhn(numero):
    numero = numero.replace(" ", "")
    if not numero.isdigit():
        return False

    suma = 0
    alternar = False

    for digito in reversed(numero):
        n = int(digito)
        if alternar:
            n *= 2
            if n > 9:
                n -= 9
        suma += n
        alternar = not alternar

    return suma % 10 == 0
