import socket
import time
import sys
import random
from protocolo import crear_mensaje, SECTORES

class NodoSensor:
    def __init__(self, sector, host="127.0.0.1", puerto=5000):
        if sector not in SECTORES:
            raise ValueError(f"Sector invalido. Elige entre: {list(SECTORES.keys())}")
        self.sector = sector
        self.host = host
        self.puerto = puerto
        self.socket_cliente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def iniciar(self):
        """Se conecta a la nave y empieza a enviar datos"""
        self.socket_cliente.connect((self.host, self.puerto))
        
        # Envia mensaje inicial de registro
        self.socket_cliente.sendall(crear_mensaje("REGISTRO", self.sector))
        print(f"[SENSOR {self.sector}] Conectado a la nave.")

        ultimo_heartbeat = 0
        try:
            while True:
                ahora = time.time()
                
                # Envía heartbeat cada 1.5s
                if ahora - ultimo_heartbeat > 1.5:
                    self.socket_cliente.sendall(crear_mensaje("HEARTBEAT", self.sector))
                    ultimo_heartbeat = ahora

                # Probabilidad reducida a 5% revisada cada 1 segundo
                if random.random() < 0.05:
                    print(f"[SENSOR {self.sector}] ¡Meteorito detectado!")
                    self.socket_cliente.sendall(crear_mensaje("DETECCION", self.sector, {"valor": 1}))
                    time.sleep(4) # Pausa para no enviar múltiples alertas seguidas
                
                time.sleep(1.0)
        except KeyboardInterrupt:
            print(f"[SENSOR {self.sector}] Desconectando sensor.")
            self.socket_cliente.close()

if __name__ == "__main__":
    # Lee el nombre del sector desde la terminal (ejemplo: python nodo_sensor.py NE)
    sector_elegido = sys.argv[1] if len(sys.argv) > 1 else "NE"
    sensor = NodoSensor(sector=sector_elegido)
    sensor.iniciar()