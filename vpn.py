import socket
import ssl
import threading
import logging
import urllib.parse
import os

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class V2RayClient:
    def __init__(self, config_url):
        self.config_url = config_url
        self.server_address = None
        self.server_port = None
        self.uuid_str = None
        self.security = "none"
        self.allow_insecure = True
        self.sni = None
        self.path = "/"
        self.host = None
        self.network = "tcp"
        self.ws_header = None
        self.socket = None
        self.connected = False
        self.lock = threading.Lock()
        self._parse_config()

    def _parse_config(self):
        try:
            parsed_url = urllib.parse.urlparse(self.config_url)
            if parsed_url.scheme != "vless":
                raise ValueError("Invalid scheme. Only 'vless' is supported.")

            userinfo = parsed_url.netloc.split("@")[0]
            self.uuid_str = userinfo

            host_port = parsed_url.netloc.split("@")[1]
            self.server_address = host_port.split(":")[0]
            self.server_port = int(host_port.split(":")[1])

            query_params = urllib.parse.parse_qs(parsed_url.query)
            self.path = query_params.get("path", ["/"])[0]
            self.security = query_params.get("security", ["none"])[0]
            self.network = query_params.get("type", ["tcp"])[0]
            self.sni = query_params.get("sni", [self.server_address])[0]
            self.host = query_params.get("host", [self.server_address])[0]

            if self.network == "ws":
                self.ws_header = {
                    "User-Agent": "Mozilla/5.0",
                    "Origin": f"https://{self.host}",
                }

        except Exception as e:
            logging.error(f"Error parsing config URL: {e}")
            raise

    def _create_socket(self):
        if self.network == "tcp":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        elif self.network == "ws":
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            raise ValueError("Invalid network type. Must be 'tcp' or 'ws'")
        return sock

    def _connect_tcp(self):
        try:
            self.socket = self._create_socket()
            if self.security == "tls":
                context = ssl.create_default_context()
                if self.allow_insecure:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                self.socket = context.wrap_socket(self.socket, server_hostname=self.sni)
            self.socket.connect((self.server_address, self.server_port))
            self.connected = True
            logging.info(
                f"Connected to {self.server_address}:{self.server_port} via TCP"
            )
        except Exception as e:
            logging.error(f"TCP Connection error: {e}")
            self.connected = False
            if self.socket:
                self.socket.close()
            self.socket = None

    def _connect_ws(self):
        try:
            self.socket = self._create_socket()
            if self.security == "tls":
                context = ssl.create_default_context()
                if self.allow_insecure:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                self.socket = context.wrap_socket(self.socket, server_hostname=self.sni)
            self.socket.connect((self.server_address, self.server_port))

            handshake = (
                f"GET {self.path} HTTP/1.1\r\n"
                f"Host: {self.host}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Sec-WebSocket-Version: 13\r\n"
            )
            if self.ws_header:
                for key, value in self.ws_header.items():
                    handshake += f"{key}: {value}\r\n"
            handshake += "\r\n"
            self.socket.sendall(handshake.encode())
            response = self.socket.recv(1024).decode()
            if "101 Switching Protocols" not in response:
                raise Exception(f"WebSocket handshake failed: {response}")
            self.connected = True
            logging.info(
                f"Connected to {self.server_address}:{self.server_port} via WebSocket"
            )
        except Exception as e:
            logging.error(f"WebSocket Connection error: {e}")
            self.connected = False
            if self.socket:
                self.socket.close()
            self.socket = None

    def connect(self):
        with self.lock:
            if self.connected:
                logging.warning("Already connected.")
                return
            if self.network == "tcp":
                self._connect_tcp()
            elif self.network == "ws":
                self._connect_ws()
            else:
                logging.error("Invalid network type.")

    def close(self):
        with self.lock:
            if self.socket:
                self.socket.close()
                self.socket = None
                self.connected = False
                logging.info("Connection closed.")

    def _generate_vless_header(self, payload):
        command = b"\x01\x00"
        address_type = b"\x01"
        address = socket.inet_aton(self.server_address)
        port = self.server_port.to_bytes(2, "big")
        header = command + address_type + address + port
        return header + payload

    def send(self, data):
        with self.lock:
            if not self.connected or not self.socket:
                logging.error("Not connected. Cannot send data.")
                return False
            try:
                if self.network == "tcp":
                    vless_header = self._generate_vless_header(data)
                    self.socket.sendall(vless_header)
                elif self.network == "ws":
                    frame = self._create_websocket_frame(data)
                    self.socket.sendall(frame)
                return True
            except Exception as e:
                logging.error(f"Error sending data: {e}")
                self.close()
                return False

    def _create_websocket_frame(self, data):
        frame = bytearray()
        opcode = 0x02  # Binary frame
        payload_len = len(data)
        frame.append(opcode | 0x80)  # Set FIN bit

        if payload_len <= 125:
            frame.append(payload_len)
        elif payload_len <= 65535:
            frame.append(126)
            frame.extend(payload_len.to_bytes(2, "big"))
        else:
            frame.append(127)
            frame.extend(payload_len.to_bytes(8, "big"))
        frame.extend(data)
        return bytes(frame)

    def receive(self, buffer_size=4096):
        with self.lock:
            if not self.connected or not self.socket:
                logging.error("Not connected. Cannot receive data.")
                return None
            try:
                if self.network == "tcp":
                    data = self.socket.recv(buffer_size)
                    if not data:
                        self.close()
                        return None
                    return data
                elif self.network == "ws":
                    frame = self.socket.recv(buffer_size)
                    if not frame:
                        self.close()
                        return None
                    return self._parse_websocket_frame(frame)
            except Exception as e:
                logging.error(f"Error receiving data: {e}")
                self.close()
                return None

    def _parse_websocket_frame(self, frame):
        if len(frame) < 2:
            return None
        fin = frame[0] & 0x80
        opcode = frame[0] & 0x0F
        masked = frame[1] & 0x80
        payload_len = frame[1] & 0x7F

        if opcode != 0x02:
            logging.warning(f"Received non-binary frame. Opcode: {opcode}")
            return None

        if payload_len == 126:
            if len(frame) < 4:
                return None
            payload_len = int.from_bytes(frame[2:4], "big")
            data_start = 4
        elif payload_len == 127:
            if len(frame) < 10:
                return None
            payload_len = int.from_bytes(frame[2:10], "big")
            data_start = 10
        else:
            data_start = 2

        if masked:
            if len(frame) < data_start + 4:
                return None
            masking_key = frame[data_start : data_start + 4]
            data_start += 4
            if len(frame) < data_start + payload_len:
                return None
            masked_payload = frame[data_start : data_start + payload_len]
            payload = bytes(
                [masked_payload[i] ^ masking_key[i % 4] for i in range(payload_len)]
            )
        else:
            if len(frame) < data_start + payload_len:
                return None
            payload = frame[data_start : data_start + payload_len]
        return payload


# if __name__ == "__main__":
#     # Example usage
#     config_url = "vless://3-TELEGRAM-NUFiLTER@NUFiLTER.fastly80-3.hosting-ip.com:80?path=%2Ftelegram-NUFiLTER%2Ctelegram-NUFiLTER%2Ctelegram-NUFiLTER%2Ctelegram-NUFiLTER%2Ctelegram-NUFiLTER%2Ctelegram-NUFiLTER%2Ctelegram-NUFiLTER%2Ctelegram-NUFiLTER%3Fed%3D8080&security=none&encryption=none&host=Dorzadim.filtero.net&type=ws#%40V2ry_Proxy%20%F0%9F%87%A8%F0%9F%87%AD%20ws4"

#     client = V2RayClient(config_url)

#     try:
#         client.connect()
#         if client.connected:
#             message = b"Hello, V2Ray server!"
#             if client.send(message):
#                 response = client.receive()
#                 if response:
#                     try:
#                         print(f"Received: {response.decode()}")
#                     except UnicodeDecodeError:
#                         print(f"Received: {response}")
#                 else:
#                     print("No response received.")
#             else:
#                 print("Failed to send message.")
#         else:
#             print("Failed to connect to the server.")
#     except Exception as e:
#         logging.error(f"An error occurred: {e}")
#     finally:
#         client.close()
