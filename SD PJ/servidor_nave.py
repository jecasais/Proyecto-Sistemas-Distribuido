"""Servidor central de la nave Aegis-Star 8-Sector.

Gestiona las conexiones TCP de los 8 sensores perimetrales, mantiene la matriz
3x3 de amenazas bajo exclusión mutua, correlaciona las detecciones para evitar
falsas alertas rojas, asigna respaldos ante caídas o cuarentenas y publica
eventos hacia la interfaz gráfica mediante una cola segura para hilos.
"""

import itertools
import socket
import threading
import json
import time
from queue import Empty, PriorityQueue, Queue

from protocolo import (
    ANILLO_PERIMETRAL,
    SECTORES,
    VECINOS_ADYACENTES,
    TIPO_REGISTRO,
    TIPO_HEARTBEAT,
    TIPO_DETECCION,
    TIPO_RESPUESTA,
    TIPO_RECONFIGURACION,
    TIPO_CUARENTENA,
    crear_mensaje,
    validar_mensaje,
)


class ServidorNave:
    """Núcleo concurrente del centro de mando.

    ``candado_matriz`` (RLock) protege la rejilla y los temporizadores;
    ``candado_sensores`` (Lock) protege el registro de conexiones, estados y
    coberturas. Los mensajes entrantes se procesan en un hilo worker que
    consume una PriorityQueue, de modo que las detecciones tengan prioridad
    sobre los heartbeats sin bloquear los hilos de recepción.
    """

    TIEMPO_INACTIVIDAD = 4.0
    INTERVALO_REVISION = 2.0
    DURACION_METEORO = 3.0
    VENTANA_SPAM = 1.0
    LIMITE_DETECCIONES = 5
    VENTANA_CORRELACION = 0.5
    CICLOS_DEDUP = 2
    PRIORIDAD_MENSAJES = {
        TIPO_DETECCION: 1,
        TIPO_HEARTBEAT: 2,
        TIPO_REGISTRO: 2,
    }

    def __init__(self, host="127.0.0.1", puerto=5000):
        """Inicializa la matriz vacía, las colas y el registro de sensores."""
        self.host = host
        self.puerto = puerto

        self.matriz_amenazas = [[0] * 3 for _ in range(3)]

        self.candado_matriz = threading.RLock()
        self.candado_sensores = threading.Lock()

        self.sensores_conectados = {}
        self.sockets_sensores = {}
        self.identidades_sockets = {}
        self.estados_sensores = {sector: "caido" for sector in ANILLO_PERIMETRAL}
        self.sensores_bloqueados = set()
        self.coberturas = {sector: [] for sector in ANILLO_PERIMETRAL}
        self.historial_detecciones = {}
        self.timers_deteccion = {}  # sector -> threading.Timer vigente
        self.timer_correlacion = None  # temporizador de la ventana de 0.5s
        self.eventos_procesados = {}  # ciclo -> sectores ya reportados
        self.ultima_alerta = "SIN AMENAZAS"

        self.cola_eventos = Queue()
        self.cola_mensajes = PriorityQueue()
        self.contador_mensajes = itertools.count()
        self.en_ejecucion = True

    def iniciar(self):
        """Abre el socket de escucha y lanza los hilos worker y de vigilancia."""
        conexion_servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conexion_servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        conexion_servidor.bind((self.host, self.puerto))
        conexion_servidor.listen(8)
        conexion_servidor.settimeout(0.5)
        print(f"[NAVE] Servidor listo en el puerto {self.puerto}")

        threading.Thread(target=self.procesar_cola_mensajes, daemon=True).start()
        threading.Thread(target=self.revisar_desconexiones, daemon=True).start()

        while self.en_ejecucion:
            try:
                socket_cliente, _ = conexion_servidor.accept()
                threading.Thread(
                    target=self.atender_sensor,
                    args=(socket_cliente,),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError as error:
                if self.en_ejecucion:
                    print(f"[ERROR NAVE] {error}")
                break

        conexion_servidor.close()

    def procesar_cola_mensajes(self):
        """Consume la PriorityQueue en un hilo dedicado.

        La tupla ``(prioridad, contador, mensaje, socket)`` usa el contador
        secuencial como desempate para que Python nunca compare dos dicts.
        """
        while self.en_ejecucion:
            try:
                _, _, mensaje, socket_cliente = self.cola_mensajes.get(timeout=0.2)
            except Empty:
                continue
            try:
                self.procesar_mensaje(mensaje, socket_cliente)
            except Exception as error:
                print(f"[NAVE] Error al procesar mensaje: {error}")
            finally:
                self.cola_mensajes.task_done()

    def atender_sensor(self, socket_cliente):
        """Lee líneas JSON de un sensor y las encola según su prioridad."""
        buffer_texto = ""
        nombre_sensor = None

        while self.en_ejecucion:
            try:
                datos = socket_cliente.recv(1024).decode("utf-8")
                if not datos:
                    break

                buffer_texto += datos
                while "\n" in buffer_texto:
                    linea, buffer_texto = buffer_texto.split("\n", 1)
                    if not linea.strip():
                        continue
                    mensaje = json.loads(linea)
                    if isinstance(mensaje, dict):
                        nombre_sensor = mensaje.get("origen") or nombre_sensor
                        # Un DETECCION puede adelantar a su REGISTRO en la
                        # PriorityQueue. Fijamos la identidad aquí para que el
                        # worker no lo confunda con un primer mensaje inválido.
                        if (
                            mensaje.get("tipo") == TIPO_REGISTRO
                            and mensaje.get("origen") in SECTORES
                        ):
                            with self.candado_sensores:
                                self.identidades_sockets.setdefault(
                                    socket_cliente, mensaje["origen"]
                                )
                    tipo = mensaje.get("tipo") if isinstance(mensaje, dict) else None
                    prioridad = self.PRIORIDAD_MENSAJES.get(tipo, 2)
                    self.cola_mensajes.put(
                        (prioridad, next(self.contador_mensajes), mensaje, socket_cliente)
                    )
                    nombre_sensor = (
                        self._sensor_del_socket(socket_cliente) or nombre_sensor
                    )
            except json.JSONDecodeError as error:
                sensor = (
                    self._sensor_del_socket(socket_cliente)
                    or nombre_sensor
                    or "desconocido"
                )
                self._poner_en_cuarentena(
                    sensor, f"paquete JSON mal formado: {error}", socket_cliente
                )
                break
            except ConnectionResetError:
                # El sensor cerró el socket de forma abrupta (apagado o falla).
                break
            except (OSError, UnicodeDecodeError) as error:
                print(f"[NAVE] Error al leer un paquete: {error}")
                break

        if nombre_sensor:
            self.registrar_desconexion(nombre_sensor)
        with self.candado_sensores:
            self.identidades_sockets.pop(socket_cliente, None)
        try:
            socket_cliente.close()
        except OSError:
            pass

    def procesar_mensaje(self, mensaje, socket_cliente):
        """Valida y aplica un mensaje ya extraído de la cola de prioridad."""
        if not isinstance(mensaje, dict):
            sensor = self._sensor_del_socket(socket_cliente) or "desconocido"
            self._poner_en_cuarentena(
                sensor, "paquete mal formado", socket_cliente
            )
            return

        origen = mensaje.get("origen")
        tipo = mensaje.get("tipo")
        identidad = self._sensor_del_socket(socket_cliente)
        sensor = identidad or (
            origen if origen in SECTORES else origen or "desconocido"
        )

        with self.candado_sensores:
            if sensor in self.sensores_bloqueados:
                # Un REGISTRO nuevo significa que el sensor fue reiniciado:
                # se le permite volver a entrar y se liberará su respaldo.
                if tipo == TIPO_REGISTRO:
                    self.sensores_bloqueados.discard(sensor)
                    self.estados_sensores[sensor] = "caido"
                else:
                    return

        es_valido, motivo = validar_mensaje(mensaje)
        if es_valido:
            datos = mensaje.get("datos") or {}
            sector_obj = datos.get("sector_objetivo", origen)
            if sector_obj not in SECTORES:
                es_valido = False
                motivo = f"Sector de detección inexistente: {sector_obj}"

        if not es_valido:
            self._poner_en_cuarentena(sensor, motivo, socket_cliente)
            return

        if identidad and origen != identidad:
            self._poner_en_cuarentena(
                identidad,
                f"el origen {origen} no coincide con el sensor conectado",
                socket_cliente,
            )
            return
        if identidad is None and tipo != TIPO_REGISTRO:
            self._poner_en_cuarentena(
                sensor, "el primer mensaje debe ser de registro", socket_cliente
            )
            return

        with self.candado_sensores:
            self.identidades_sockets.setdefault(socket_cliente, sensor)
            self.sensores_conectados[sensor] = time.time()
            self.sockets_sensores[sensor] = socket_cliente
            estado_anterior = self.estados_sensores.get(sensor)
            if not str(estado_anterior).startswith("cubriendo"):
                self.estados_sensores[sensor] = "activo"

        if tipo == TIPO_REGISTRO:
            print(f"[NAVE] Sensor {sensor} conectado con éxito.")
            try:
                socket_cliente.sendall(
                    crear_mensaje(TIPO_RESPUESTA, "NAVE", {"estado": "OK"})
                )
            except OSError as error:
                print(f"[NAVE] No se pudo confirmar el registro de {sensor}: {error}")
            self._publicar_evento(f"Sensor {sensor} conectado con éxito.")
            # Si otro sensor estaba respaldando este sector, se libera.
            self._liberar_respaldo(sensor)

        elif tipo == TIPO_HEARTBEAT:
            pass

        elif tipo == TIPO_DETECCION:
            if self._registrar_deteccion(sensor):
                self._poner_en_cuarentena(
                    sensor, "ráfaga anómala de detecciones (spam)", socket_cliente
                )
                return

            datos = mensaje["datos"]
            sector_obj = datos.get("sector_objetivo", origen)
            ciclo = datos.get("ciclo")
            if self._es_reporte_duplicado(ciclo, sector_obj):
                print(
                    f"[DEDUP] Reporte duplicado ignorado: ciclo={ciclo}, "
                    f"sector={sector_obj} (sensor {sensor})."
                )
                return

            valor = datos.get("valor", 1)
            fila, columna = SECTORES[sector_obj]

            with self.candado_matriz:
                self.matriz_amenazas[fila][columna] = valor
                self._reiniciar_temporizador(sector_obj, fila, columna)
                estado_crudo = self._calcular_amenaza()
                if "ROJA" in estado_crudo:
                    # Ventana de correlación: un 1 aislado aún puede ser el
                    # primer paso de un tránsito tangencial (0.35s de desfase).
                    if self.timer_correlacion is None:
                        self.timer_correlacion = threading.Timer(
                            self.VENTANA_CORRELACION, self._confirmar_correlacion
                        )
                        self.timer_correlacion.start()
                    estado_alerta = "CORRELACIONANDO AMENAZA"
                else:
                    if self.timer_correlacion is not None:
                        self.timer_correlacion.cancel()
                        self.timer_correlacion = None
                    estado_alerta = estado_crudo

            self._actualizar_alerta(estado_alerta)

            texto_log = (
                f"Sensor {sensor} detectó objeto en {sector_obj} -> Estado: {estado_alerta}"
            )
            print(texto_log)
            self._publicar_evento(texto_log, estado_alerta)

    def _reiniciar_temporizador(self, sector_obj, fila, columna):
        """Reinicia la limpieza del sector tras ``DURACION_METEORO``.

        Se llama con ``candado_matriz`` adquirido. Si el sector ya tenía un
        temporizador pendiente se cancela, de modo que una nueva detección
        extienda la vigencia de la amenaza en vez de ser borrada por el timer
        anterior. La comparación de identidad cubre el caso en que el timer
        antiguo ya estuviera ejecutándose cuando llega la nueva detección.
        """
        anterior = self.timers_deteccion.pop(sector_obj, None)
        if anterior is not None:
            anterior.cancel()

        def apagar_deteccion():
            """Limpia la celda solo si este timer sigue siendo el vigente."""
            with self.candado_matriz:
                if self.timers_deteccion.get(sector_obj) is not temporizador:
                    return
                self.matriz_amenazas[fila][columna] = 0
                self.timers_deteccion.pop(sector_obj, None)

            nuevo_estado = self.evaluar_amenaza()
            self._actualizar_alerta(nuevo_estado)
            self._publicar_evento(
                f"Sector {sector_obj} libre de amenazas.", nuevo_estado
            )

        temporizador = threading.Timer(self.DURACION_METEORO, apagar_deteccion)
        self.timers_deteccion[sector_obj] = temporizador
        temporizador.start()

    def evaluar_amenaza(self):
        """Devuelve el estado de alerta calculado bajo ``candado_matriz``."""
        with self.candado_matriz:
            return self._calcular_amenaza()

    def _actualizar_alerta(self, estado):
        """Publica el cambio de estado y dispara la evasión al pasar a roja."""
        with self.candado_sensores:
            activar_evasion = "ROJA" in estado and self.ultima_alerta != estado
            self.ultima_alerta = estado
        if activar_evasion:
            self._activar_evasion()

    def _activar_evasion(self):
        """Envía EVASION con escudos activos a todos los sensores operativos."""
        texto = "[ALERTA ROJA] Maniobra de evasión activada: escudos desplegados."
        print(texto)

        orden = crear_mensaje(
            TIPO_RECONFIGURACION, "NAVE", {"accion": "EVASION", "escudos": "ACTIVAR"}
        )
        with self.candado_sensores:
            destinos = [
                sensor for sensor in self.sockets_sensores
                if sensor not in self.sensores_bloqueados
            ]
        for sensor in destinos:
            self._enviar_orden_a_sensor(sensor, orden)

        self._publicar_evento(texto)

    def _registrar_deteccion(self, sensor):
        """Cuenta las detecciones recientes para detectar ráfagas de spam."""
        ahora = time.time()
        with self.candado_sensores:
            recientes = [
                instante for instante in self.historial_detecciones.get(sensor, [])
                if ahora - instante <= self.VENTANA_SPAM
            ]
            recientes.append(ahora)
            self.historial_detecciones[sensor] = recientes
            return len(recientes) >= self.LIMITE_DETECCIONES

    def _es_reporte_duplicado(self, ciclo, sector):
        """Descarta dos reportes del mismo objeto y ciclo PRNG.

        Se conservan solo los dos ciclos más recientes para no fugar memoria.
        Los ataques alien no traen ``ciclo`` y por eso no se deduplican.
        """
        if not isinstance(ciclo, int):
            return False
        with self.candado_matriz:
            for viejo in [
                valor for valor in self.eventos_procesados
                if valor < ciclo - self.CICLOS_DEDUP
            ]:
                del self.eventos_procesados[viejo]
            sectores = self.eventos_procesados.setdefault(ciclo, set())
            if sector in sectores:
                return True
            sectores.add(sector)
            return False

    def _confirmar_correlacion(self):
        """Resuelve la ventana de 0.5s: confirma roja o descarta falsa alarma."""
        with self.candado_matriz:
            self.timer_correlacion = None
            estado = self._calcular_amenaza()

        self._actualizar_alerta(estado)
        if "ROJA" in estado:
            texto = "[ALERTA ROJA] Colisión confirmada tras la ventana de correlación."
        else:
            texto = (
                "[CORRELACIÓN] Tránsito tangencial confirmado; "
                "se descarta la alerta roja."
            )
        print(texto)
        self._publicar_evento(texto, estado)

    def _asignar_respaldo(self, sector_objetivo):
        """Pide a un vecino activo que cubra ``sector_objetivo``.

        Reutilizado por caídas por heartbeat y por cuarentenas. Devuelve el
        nombre del vecino que aceptó la orden o ``None`` si no hay ninguno.
        """
        with self.candado_sensores:
            ahora = time.time()
            candidatos = [
                vecino for vecino in VECINOS_ADYACENTES.get(sector_objetivo, ())
                if vecino in self.sensores_conectados
                and ahora - self.sensores_conectados[vecino] <= self.TIEMPO_INACTIVIDAD
                and vecino not in self.sensores_bloqueados
            ]
            candidatos.sort(
                key=lambda vecino: str(
                    self.estados_sensores.get(vecino)
                ).startswith("cubriendo")
            )

        orden = crear_mensaje(
            TIPO_RECONFIGURACION,
            "NAVE",
            {"accion": "CUBRIR", "sector_objetivo": sector_objetivo},
        )
        for candidato in candidatos:
            if self._enviar_orden_a_sensor(candidato, orden):
                with self.candado_sensores:
                    cubiertos = self.coberturas.setdefault(candidato, [])
                    if sector_objetivo not in cubiertos:
                        cubiertos.append(sector_objetivo)
                    detalle = ", ".join(sorted(cubiertos))
                    self.estados_sensores[candidato] = f"cubriendo ({detalle})"
                return candidato
            print(f"[NAVE] {candidato} no disponible; se intenta otro vecino.")
        return None

    def _liberar_respaldo(self, sensor):
        """Ordena LIBERAR a los vecinos que estaban cubriendo ``sensor``."""
        with self.candado_sensores:
            responsables = [
                vecino for vecino, sectores in self.coberturas.items()
                if sensor in sectores
            ]
        if not responsables:
            return

        orden = crear_mensaje(
            TIPO_RECONFIGURACION,
            "NAVE",
            {"accion": "LIBERAR", "sector_objetivo": sensor},
        )
        liberados = []
        for vecino in responsables:
            if self._enviar_orden_a_sensor(vecino, orden):
                with self.candado_sensores:
                    cubiertos = self.coberturas.get(vecino, [])
                    if sensor in cubiertos:
                        cubiertos.remove(sensor)
                    if cubiertos:
                        self.estados_sensores[vecino] = (
                            f"cubriendo ({', '.join(sorted(cubiertos))})"
                        )
                    else:
                        self.estados_sensores[vecino] = "activo"
                liberados.append(vecino)

        if liberados:
            texto = (
                f"[RESTAURACIÓN] Sensor {sensor} reconectado. "
                f"{', '.join(liberados)} liberó la cobertura de su sector."
            )
            print(texto)
            self._publicar_evento(texto)

    def _sensor_del_socket(self, socket_cliente):
        """Devuelve el sector asociado a un socket, o ``None`` si es nuevo."""
        with self.candado_sensores:
            for sensor, socket_sensor in self.sockets_sensores.items():
                if socket_sensor is socket_cliente:
                    return sensor
        return None

    def _poner_en_cuarentena(self, sensor, motivo, socket_cliente):
        """Aísla al sensor anómalo, notifica CUARENTENA y asigna un respaldo."""
        with self.candado_sensores:
            self.sensores_bloqueados.add(sensor)
            self.sensores_conectados.pop(sensor, None)
            self.sockets_sensores.pop(sensor, None)
            if sensor in self.estados_sensores:
                self.estados_sensores[sensor] = "cuarentena"

        try:
            socket_cliente.sendall(crear_mensaje(TIPO_CUARENTENA, "NAVE"))
        except OSError as error:
            print(f"[NAVE] No se pudo notificar la cuarentena de {sensor}: {error}")

        texto = f"[CUARENTENA] Sensor {sensor} aislado por anomalía: {motivo}."
        if sensor in SECTORES:
            vecino = self._asignar_respaldo(sensor)
            if vecino:
                texto += f" {vecino} cubrirá su sector."
        print(texto)
        self._publicar_evento(texto)

    def revisar_desconexiones(self):
        """Detecta heartbeats vencidos cada ``INTERVALO_REVISION`` segundos."""
        while self.en_ejecucion:
            ahora = time.time()
            with self.candado_sensores:
                vencidos = [
                    sensor for sensor, ultimo in self.sensores_conectados.items()
                    if ahora - ultimo > self.TIEMPO_INACTIVIDAD
                ]
            for sensor in vencidos:
                self.registrar_desconexion(sensor)
            time.sleep(self.INTERVALO_REVISION)

    def _enviar_orden_a_sensor(self, sensor, mensaje, estado=None):
        """Envía una orden verificando bajo candado que el socket siga activo.

        El socket puede haberse cerrado en otro hilo entre la selección del
        vecino y el envío. Si ocurre, se descarta el sensor y se devuelve False
        para que el llamador pueda intentar con otro vecino.
        """
        with self.candado_sensores:
            if (
                sensor not in self.sensores_conectados
                or sensor in self.sensores_bloqueados
            ):
                return False
            socket_sensor = self.sockets_sensores.get(sensor)
            if socket_sensor is None:
                return False
            try:
                socket_sensor.sendall(mensaje)
            except OSError as error:
                print(f"[NAVE] No se pudo enviar orden a {sensor}: {error}")
                self.sensores_conectados.pop(sensor, None)
                self.sockets_sensores.pop(sensor, None)
                if sensor in self.estados_sensores:
                    self.estados_sensores[sensor] = "caido"
                return False
            if estado and sensor in self.estados_sensores:
                self.estados_sensores[sensor] = estado
            return True

    def registrar_desconexion(self, sensor):
        """Quita al sensor caído y delega su sector en un vecino disponible."""
        with self.candado_sensores:
            if sensor not in self.sensores_conectados:
                self.sockets_sensores.pop(sensor, None)
                return

            self.sensores_conectados.pop(sensor, None)
            socket_caido = self.sockets_sensores.pop(sensor, None)
            bloqueado = sensor in self.sensores_bloqueados
            if sensor in self.estados_sensores:
                self.estados_sensores[sensor] = (
                    "cuarentena" if bloqueado else "caido"
                )

        if socket_caido:
            try:
                socket_caido.close()
            except OSError:
                pass

        if bloqueado:
            # La cuarentena ya asignó el respaldo; no repetir la orden.
            return

        vecino = self._asignar_respaldo(sensor)

        if vecino:
            texto = (
                f"[DESCONEXIÓN] Sensor {sensor} sin heartbeat por más de "
                f"{self.TIEMPO_INACTIVIDAD:.0f}s. {vecino} cubrirá su sector."
            )
        else:
            texto = (
                f"[DESCONEXIÓN] Sensor {sensor} caído. "
                "No hay vecinos activos disponibles para cubrirlo."
            )
        print(texto)
        self._publicar_evento(texto)

    def _publicar_evento(self, texto, alerta=None):
        """Copia matriz y estados hacia ``cola_eventos`` para la interfaz."""
        with self.candado_matriz:
            estado = alerta if alerta is not None else self._calcular_amenaza()
            evento = {
                "tipo": "LOG",
                "texto": texto,
                "matriz": [fila[:] for fila in self.matriz_amenazas],
                "alerta": estado,
            }
        with self.candado_sensores:
            evento["sensores"] = dict(self.estados_sensores)

        self.cola_eventos.put(evento)

    def _calcular_amenaza(self):
        """Aplica la regla circular: un 1 sin vecinos activos es colisión.

        Dos impactos en sectores opuestos se consideran dos choques, mientras
        que varios 1 consecutivos sobre ``ANILLO_PERIMETRAL`` son tránsito.
        """
        activos = {
            sector for sector in ANILLO_PERIMETRAL
            if self.matriz_amenazas[SECTORES[sector][0]][SECTORES[sector][1]] == 1
        }
        if not activos:
            return "SIN AMENAZAS"

        total = len(ANILLO_PERIMETRAL)
        for sector in activos:
            indice = ANILLO_PERIMETRAL.index(sector)
            vecinos = (
                ANILLO_PERIMETRAL[(indice - 1) % total],
                ANILLO_PERIMETRAL[(indice + 1) % total],
            )
            if not activos.intersection(vecinos):
                return "ALERTA ROJA: COLISION INMINENTE"
        return "TRANSITO SEGURO (TANGENCIAL)"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Servidor Central Aegis-Star 8-Sector")
    parser.add_argument("--host", default="127.0.0.1", help="IP de escucha del servidor")
    parser.add_argument("--puerto", type=int, default=5000, help="Puerto del servidor")
    args = parser.parse_args()

    nave = ServidorNave(host=args.host, puerto=args.puerto)
    nave.iniciar()
