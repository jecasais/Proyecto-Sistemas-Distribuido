import socket
import threading
import json
import time
from queue import Queue
from protocolo import SECTORES, crear_mensaje

class ServidorNave:
    def __init__(self, host="127.0.0.1", puerto=5000):
        self.host = host
        self.puerto = puerto
        
        # Matriz binaria 3x3 (0 = seguro, 1 = meteorito)
        self.matriz_amenazas = [[0]*3 for _ in range(3)]
        
        # Candado para que dos hilos no cambien la matriz al mismo tiempo
        self.candado_matriz = threading.Lock()
        
        # Guardamos la ultima vez que cada sensor reportó que sigue vivo
        self.sensores_conectados = {}
        
        # Cola para enviar datos a la interfaz grafica
        self.cola_eventos = Queue()
        self.en_ejecucion = True

    def iniciar(self):
        """Abre el puerto y se queda esperando conexiones de los sensores"""
        conexion_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conexion_servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conexion_servidor.bind((self.host, self.puerto))
        conexion_servidor.listen(8)
        print(f"[NAVE] Servidor listo en el puerto {self.puerto}")

        # Hilo secundario que revisa si algun sensor se desconectó
        threading.Thread(target=self.revisar_desconexiones, daemon=True).start()

        while self.en_ejecucion:
            try:
                socket_cliente, direccion = conexion_servidor.accept()
                # Atiende a cada sensor en un hilo independiente
                threading.Thread(target=self.atender_sensor, args=(socket_cliente,), daemon=True).start()
            except Exception as error:
                print(f"[ERROR NAVE] {error}")

    def atender_sensor(self, socket_cliente):
        """Escucha los mensajes que envia un sensor en especifico"""
        buffer_texto = ""
        nombre_sensor = None
        
        while self.en_ejecucion:
            try:
                datos = socket_cliente.recv(1024).decode('utf-8')
                if not datos:
                    break
                
                buffer_texto += datos
                while "\n" in buffer_texto:
                    linea, buffer_texto = buffer_texto.split("\n", 1)
                    mensaje = json.loads(linea)
                    nombre_sensor = mensaje.get("origen")
                    self.procesar_mensaje(mensaje, socket_cliente)
            except:
                break

        if nombre_sensor:
            print(f"[NAVE] El sensor {nombre_sensor} se ha desconectado.")
            self.sensores_conectados.pop(nombre_sensor, None)
            socket_cliente.close()

    def procesar_mensaje(self, mensaje, socket_cliente):
        """Lee el tipo de mensaje y actualiza la matriz o el registro"""
        tipo = mensaje["tipo"]
        origen = mensaje["origen"]
        
        # Guardamos la hora actual para saber que el sensor esta activo
        self.sensores_conectados[origen] = time.time()

        if tipo == "REGISTRO":
            print(f"[NAVE] Sensor {origen} conectado con exito.")
            socket_cliente.sendall(crear_mensaje("RESPUESTA", "NAVE", {"estado": "OK"}))

        elif tipo == "HEARTBEAT":
            # Solo actualiza la señal de vida
            pass

        elif tipo == "DETECCION":
            fila, columna = SECTORES[origen]
            valor = mensaje["datos"].get("valor", 1)
            
            # 1. Modificamos la matriz a 1
            with self.candado_matriz:
                self.matriz_amenazas[fila][columna] = valor
                estado_alerta = self.evaluar_amenaza()

            texto_log = f"Sensor {origen} detectó objeto -> Estado: {estado_alerta}"
            print(texto_log)
            
            self.cola_eventos.put({
                "tipo": "LOG", 
                "texto": texto_log, 
                "matriz": self.matriz_amenazas, 
                "alerta": estado_alerta
            })

            # 2. Función interna para apagar el 1 después de 3 segundos
            def apagar_deteccion():
                with self.candado_matriz:
                    self.matriz_amenazas[fila][columna] = 0
                    nuevo_estado = self.evaluar_amenaza()
                
                self.cola_eventos.put({
                    "tipo": "LOG", 
                    "texto": f"Sector {origen} libre de amenazas.", 
                    "matriz": self.matriz_amenazas, 
                    "alerta": nuevo_estado
                })

            # Programa el apagado a los 3 segundos
            threading.Timer(3.0, apagar_deteccion).start()

    def evaluar_amenaza(self):
        """Cuenta los unos en la matriz para decidir el nivel de alerta"""
        total_unos = sum(fila.count(1) for fila in self.matriz_amenazas)

        if total_unos == 0:
            return "SIN AMENAZAS"
        elif total_unos == 1:
            return "ALERTA ROJA: COLISION INMINENTE"
        else:
            return "TRANSITO SEGURO (TANGENCIAL)"

    def revisar_desconexiones(self):
        """Revisa cada 2 segundos si algun sensor dejó de responder"""
        while self.en_ejecucion:
            ahora = time.time()
            for sensor, ultimo_reporte in list(self.sensores_conectados.items()):
                if ahora - ultimo_reporte > 4.0:
                    print(f"[ALERTA] El sensor {sensor} no responde.")
            time.sleep(2)

if __name__ == "__main__":
    nave = ServidorNave()
    nave.iniciar()