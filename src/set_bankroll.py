"""
set_bankroll.py
Helper de linea de comandos para decirle al sistema cuanto bankroll tienes,
sin tener que editar JSON a mano. El bankroll se guarda en
data/bankroll.json y main.py lo usa para sugerir cuanto apostar en cada
pick (Kelly fraccionado, ver model.suggested_stake).

Uso:
    python -m src.set_bankroll 500        # fija tu bankroll en $500
    python -m src.set_bankroll +50        # le suma 50 al bankroll actual
    python -m src.set_bankroll -30        # le resta 30 al bankroll actual
    python -m src.set_bankroll            # solo muestra el bankroll actual
"""

import sys

from . import storage


def main():
    args = sys.argv[1:]

    if not args:
        current = storage.load_bankroll()
        if current is None:
            print("No tienes bankroll configurado todavia. Usa: python -m src.set_bankroll <monto>")
        else:
            print(f"Bankroll actual: ${current:,.2f}")
        return

    raw = args[0].strip()

    try:
        if raw.startswith("+") or raw.startswith("-"):
            delta = float(raw)
            new_value = storage.adjust_bankroll(delta)
            print(f"Bankroll ajustado ({raw}) -> ${new_value:,.2f}")
        else:
            amount = float(raw)
            if amount < 0:
                print("El bankroll no puede ser negativo.")
                return
            storage.save_bankroll(amount)
            print(f"Bankroll fijado en ${amount:,.2f}")
    except ValueError:
        print(f"No entendi '{raw}'. Usa un numero, ej: 500, +50, -30")


if __name__ == "__main__":
    main()
