"""Orquestador multiproceso de los 8 nodos sensores Aegis-Star."""

import subprocess
import sys
import time
import argparse
from pathlib import Path
from protocolo import ANILLO_PERIMETRAL


def main():
    """Lanza los 8 sensores y propaga host, puerto y modos de simulación."""
    parser = argparse.ArgumentParser(description="Lanzador de los 8 Nodos Sensores Aegis-Star")
    parser.add_argument("--alien", default=None, help="Sector a simular como controlado por aliens (ej: E)")
    parser.add_argument("--falla", default=None, help="Sector a simular con caída repentina (ej: N)")
    parser.add_argument("--host", default="127.0.0.1", help="IP del Servidor Central")
    parser.add_argument("--puerto", type=int, default=5000, help="Puerto del Servidor Central")
    args = parser.parse_args()

    procesos = []
    ruta_sensor = Path(__file__).resolve().parent / "nodo_sensor.py"
    print("Iniciando red periférica de 8 sensores...")

    try:
        for sector in ANILLO_PERIMETRAL:
            modo = "normal"
            if args.alien and sector == args.alien.upper():
                modo = "alien"
            elif args.falla and sector == args.falla.upper():
                modo = "falla"

            proc = subprocess.Popen(
                [
                    sys.executable, "-u", str(ruta_sensor), sector,
                    "--host", args.host,
                    "--puerto", str(args.puerto),
                    "--modo", modo,
                ]
            )
            procesos.append((sector, proc))
            time.sleep(0.1)  # Pequeño escalonamiento al conectar

        print("Los 8 sensores están operativos. Presiona Ctrl+C para detener toda la red.")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nDeteniendo todos los nodos sensores...")
        for sector, proc in procesos:
            proc.terminate()
        for sector, proc in procesos:
            proc.wait()
        print("Red de sensores apagada limpiamente.")


if __name__ == "__main__":
    main()