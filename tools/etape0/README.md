# Essais de l'étape 0

`ahv_etape0.py` prouve, sur une **VM de test**, le chemin technique du futur plugin
Bareos pour Nutanix AHV : point de récupération, régions modifiées, lecture par
Volume Group, restauration en nouvelle VM.

Le script a été vérifié contre un faux Prism, **jamais contre un vrai cluster** :
les formats d'appel viennent des spécifications OpenAPI de Nutanix. Un échec au
premier essai réel est possible ; le message d'erreur complet permet de corriger.

## Prérequis

- Python 3.9 ou plus, aucune dépendance.
- Un compte Prism Central administrateur pour ces essais (le compte de service
  restreint viendra ensuite).
- Une VM de test sans importance, de préférence **petite et à un seul disque**.
- Pour `read` et `restore` : une VM Linux **sur le même cluster** que la VM de test,
  qui joue la proxy ; le script s'y lance en root.

Le mot de passe est demandé au clavier (ou lu dans la variable `AHV_PASSWORD`).
Remplacer `--insecure` par `--ca /chemin/ca.pem` dès que l'autorité interne est disponible.

## Ordre des essais

| # | Commande | Où | Modifie le cluster | Ce que l'essai prouve |
|---|---|---|---|---|
| 1 | `check --vm VM` | n'importe où | non | versions d'API, lecture de la VM |
| 2 | `snapshot --vm VM` | n'importe où | crée puis supprime un point | licence et droits sur les points de récupération |
| 3 | `cbt --vm VM` | poste joignant aussi l'IP du cluster | deux points, supprimés | régions modifiées, part du disque à lire |
| 4 | `read --vm VM` | proxy, root | point + Volume Group, supprimés | lecture par Volume Group, débit, identification du disque |
| 5 | `restore --vm VM` | proxy, root | crée une VM `bareos-etape0-…` | écriture puis clonage vers une nouvelle VM |
| 6 | `cleanup` puis `cleanup --yes` | n'importe où | supprime les objets `bareos-etape0-*` | — |

Exemple :

```
python3 ahv_etape0.py --pc ADRESSE_PRISM_CENTRAL --insecure check --vm ma-vm-test
```

Pendant `cbt`, le script attend deux minutes (`--wait`) : écrire alors quelques
fichiers dans la VM de test, pour que l'incrémentale ait quelque chose à voir.

Après `restore`, la nouvelle VM est **éteinte et sans carte réseau**. La démarrer
depuis Prism, vérifier à la console qu'elle démarre, l'éteindre, puis lancer `cleanup --yes`.

## Sécurité

- Tout objet créé est nommé `bareos-etape0-…` ; `cleanup` ne touche à rien d'autre.
- Les points de récupération expirent seuls au bout de 4 heures.
- La VM de test n'est jamais modifiée, arrêtée ni redémarrée.
- En écriture, le script ne vise que des disques apparus après l'attachement du
  Volume Group cible, de la taille exacte attendue, et vérifiés vides.
- En cas d'erreur ou de Ctrl-C, le nettoyage s'exécute ; `cleanup` sert de filet.

## Résultats à me renvoyer

La sortie complète de chaque commande. Les lignes les plus utiles :

- `snapshot` : réussite ou message d'erreur (licence, droits) ;
- `cbt` : pourcentage du disque à lire, nombre de régions et d'appels, « métadonnées
  de la 1re réponse » ;
- `read` : débit en Mio/s, et les lignes `liens by-id` et `lsblk` (elles disent
  comment le plugin reconnaîtra un disque à coup sûr) ;
- `restore` : la VM démarre-t-elle ? (noter BIOS ou UEFI).
