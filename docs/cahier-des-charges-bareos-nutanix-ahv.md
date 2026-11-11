# Cahier des charges v2 : plugin Bareos pour Nutanix AHV

Document de départ pour le développement. Il remplace la v1 : même besoin, mais les
choix structurants sont tranchés, les cas limites listés et les critères d'acceptation
rendus mesurables.

Il couvre deux lots indépendants :

- **Lot A** (§ 1 à 16) : le plugin File Daemon de sauvegarde image des VM AHV ;
Le § 19 confronte l'ensemble au guide de l'ANSSI « Sauvegarde des systèmes
d'information – Les fondamentaux » (v1.1) et liste les changements qui en découlent.

- **Lot C** (§ 18) : ReaR (Relax-and-Recover) avec Bareos, sans développement, comme
  filet de sécurité immédiat et point de comparaison chiffré pour le lot A ;
- **Lot B** (§ 17) : un plugin Director qui pousse le résultat de chaque job vers
  Zabbix. Petit, sans lien technique avec Nutanix, utile à tous les jobs existants.
  Il est livré **en premier** : il rend testable le critère « un échec remonte dans
  Zabbix » dès l'étape 1 du lot A.

**Niveau de confiance des faits techniques**

| Marque | Signification |
|---|---|
| [B] | Vérifié dans le code source `bareos/bareos` (master, novembre 2026). |
| [N] | Lu dans les SDK Python officiels Nutanix v4 (générés depuis les spécifications OpenAPI). |
| [S] | Lu dans les spécifications OpenAPI fournies par le client (Prism v4.4, Data Protection v4.4, Volumes v4.3, VMM v4.3, IAM v4.1.b3, Cluster Management v4.4, Networking v4.4, Security v4.2, Monitoring v4.3). |
| [?] | Non vérifié (documentation Nutanix inaccessible lors de l'étude, ou source indirecte). **À confirmer sur le cluster de recette avant de coder dessus.** |

---

## 1. Contexte

- Cluster Nutanix AHV, environ 120 VM Linux (Rocky 8/9, CentOS 7, Ubuntu 16.04, Debian 12).
- Bareos 25.1 communautaire ; Director et Storage sur un serveur physique Rocky 9 hors cluster.
- Production sans accès Internet ; paquets distribués par un Nexus interne.
- Supervision Zabbix.
- Les VM sont déjà sauvegardées par agent (fichiers). Il manque une sauvegarde **image**
  sans agent, pour recréer rapidement une VM complète.

Il n'existe pas de plugin AHV pour Bareos. Le plugin de Bacula Enterprise peut servir de
référence **fonctionnelle** par sa documentation publique ; aucun code n'en est repris.

## 2. Objectif, périmètre et non-objectifs

**Objectif.** Un plugin Python pour le File Daemon Bareos (`python3-fd`) qui sauvegarde
et restaure des VM AHV au niveau image, sans agent dans les VM, via l'API Prism.

**Dans le périmètre** : Full, incrémentale par régions modifiées, restauration en
nouvelle VM, restauration d'un disque en fichier brut, découverte des VM, nettoyage
garanti, paquet RPM, documentation française.

**Hors périmètre (v1 du plugin)** — à refuser proprement, avec un message clair, plutôt
qu'à traiter à moitié :

- restauration de fichiers individuels depuis l'image (l'agent fichiers existant couvre ce besoin) ;
- VM Windows et cohérence VSS ;
- contenu des vTPM (la configuration vTPM est restaurable, pas son contenu [?]) ;
- Volume Groups attachés à une VM (signalés par un avertissement, non sauvegardés) ;
- hyperviseurs autres qu'AHV, réplication, « instant recovery » ;
- Always Incremental et VirtualFull : Bareos ne sait pas consolider côté serveur des
  données de plugin [B] (documentation Always Incremental : « only suitable for
  file-based backups »). Le schéma de sauvegarde est donc Full périodique + incrémentales.

## 3. Ce que l'étude du code Bareos impose

Ces constats corrigent la v1 et orientent la conception.

1. **Le plugin oVirt n'existe plus** : supprimé de Bareos (PR #1617) [B]. Les références
   à étudier sont VMware, Incus et Proxmox.
2. **Le plugin Proxmox ne fait que du Full** : il encapsule `vzdump` et passe un
   descripteur de fichier au cœur de Bareos (`iostat_do_in_core`) [B]. Il est le modèle
   pour la **performance d'E/S**, pas pour les incrémentales.
3. **Le plugin VMware est le modèle pour les incrémentales** : il stocke l'état CBT de
   chaque disque dans un *Restore Object* (`FT_RESTORE_FIRST`). Bareos renvoie ces
   objets au plugin au démarrage du job suivant, et au début d'une restauration [B].
   → **L'état de la chaîne vit dans le catalogue Bareos, pas dans un fichier local sur
   la VM proxy.** La proxy reste jetable et remplaçable.
4. **Un plugin ne peut pas changer le niveau d'un job.** La « bascule en Full » de la v1
   est donc, comme pour VMware (`fallback_to_full_cbt`) [B] : un job de niveau
   Incrémental qui contient toutes les données du disque, terminé avec un avertissement
   recommandant un vrai Full.
5. **Événements disponibles** [B] : `bEventStartBackupJob`, `bEventEndBackupJob`,
   `bEventStartRestoreJob`, `bEventEndRestoreJob`, `bEventJobEnd`,
   `bEventCancelCommand`, `bEventRestoreObject`, `bEventEndFileSet`. Le nettoyage doit
   s'accrocher à `EndBackupJob`, `JobEnd` **et** `CancelCommand`. Aucun ne se déclenche
   si le FD est tué (`kill -9`, redémarrage de la proxy) : voir § 8.
6. **Convention de nommage** : préfixe en majuscules (`@PROXMOX/…`, `@INCUS/…`) [B].
   On adopte `@NUTANIX-AHV/…`.
7. **Options** : la classe de base gère déjà un fichier d'options et les options
   multi-valeurs [B] ; on la réutilise au lieu d'écrire un analyseur.

## 4. Ce que l'étude de l'API Nutanix impose

1. **Cible : API v4 uniquement, via Prism Central.** Les API v0.8/v1/v2/v3 sont
   annoncées dépréciées et non supportées à partir de la version prévue au T4 2026 [?].
   Écrire un plugin neuf sur v3 serait écrire du code déjà condamné. Plancher :
   **PC pc.2024.3 / AOS 7.0** (disponibilité générale de v4) [?]. v4 se parle sur Prism
   Central, pas sur Prism Element [?] — sauf le second appel des régions modifiées.
   Si le cluster est en dessous du plancher, c'est une **décision à prendre avant de
   coder** (§ 16) : mise à niveau, ou backend v3 assumé comme temporaire.
2. **Point de récupération** [N] : `POST /api/dataprotection/v4.0/config/recovery-points`
   avec `name`, `expirationTime`, `recoveryPointType` (`CRASH_CONSISTENT` ou
   `APPLICATION_CONSISTENT`) et `vmRecoveryPoints[].vmExtId`. Les points de disque sont
   dans `vmRecoveryPoints[].diskRecoveryPoints[]` (`diskRecoveryPointExtId`, `diskExtId`).
   Suppression par `DELETE`, expiration modifiable par `$actions/set-expiration-time`.
3. **Régions modifiées, en deux temps** [N] :
   - sur Prism Central, `…/recovery-points/{id}/$actions/discover-cluster`
     (`operation = COMPUTE_CHANGED_REGIONS`) renvoie `clusterIp` et `jwtToken` ;
   - sur ce `clusterIp`, `…/content/recovery-points/{rp}/vm-recovery-points/{vmrp}/disk-recovery-points/{id}/$actions/compute-changed-regions`
     avec `offset`, `length`, `blockSizeByte` (32768 à 262144) et, pour une
     incrémentale, les trois identifiants `reference…ExtId`. Réponse : liste de
     `{offset, length, regionType}` avec `regionType` = `ZEROED` ou `REGULAR`.
   - Conséquence réseau : la proxy doit joindre **Prism Central et l'IP du cluster**
     sur 9440, et le certificat des deux doit être vérifiable.
   - Signalements de terrain (non officiels) [?] : jeton expirant vers 15 minutes,
     pagination capricieuse, résultats faux au-delà d'environ 4000 régions sur
     certaines versions. → Le client **interroge par fenêtres bornées** (`offset` +
     `length`), renouvelle le jeton avant échéance, et **valide chaque réponse** :
     régions triées, sans chevauchement, dans la fenêtre demandée. Toute incohérence
     déclenche le repli en données complètes pour ce disque, jamais une sauvegarde
     silencieusement incomplète.
4. **Lecture des données** [N] : un Volume Group (`usageType = TEMPORARY`, prévu pour
   cet usage) reçoit un disque créé depuis le point de disque
   (`diskDataSourceReference.entityType = DISK_RECOVERY_POINT`), puis est attaché à la
   proxy par `$actions/attach-vm` ou à un initiateur par `$actions/attach-iscsi-client`.
5. **Restauration** [N] : `POST /api/vmm/v4.0/ahv/config/vms`. Le clonage d'un disque de
   Volume Group vers un disque de VM (`VolumeDiskReference`) n'existe qu'à partir de
   **vmm v4.1** ; en v4.0 seuls `ImageReference` et `VmDiskReference` sont acceptés.
   Le NIC peut être créé déconnecté (`backingInfo.isConnected = false`).
6. **Limites d'API** : limitation de débit (HTTP 429) appliquée par Prism Central et
   rencontrée par d'autres éditeurs [?] ; en-tête `NTNX-Request-Id` pour l'idempotence
   et `If-Match` (ETag) sur les modifications [N] ; opérations asynchrones suivies par
   `GET /api/prism/v4.0/config/tasks/{id}` [N].
7. **Authentification** [N] : Basic ou clé d'API (`X-ntnx-api-key`). On privilégie la
   clé d'API d'un compte de service. L'existence d'un rôle intégré adapté n'est pas
   confirmée [?] ; d'autres éditeurs exigent un rôle administrateur [?]. La liste
   minimale de permissions est un **livrable de l'étape 0**, établie par essais sur la
   recette.

8. **Apports des spécifications OpenAPI fournies** (Prism v4.4) [S] :
   - chaque opération y déclare ses **rôles autorisés** (`x-permissions`), sa **limite
     de débit** par taille de Prism Central (`x-rate-limit`) et sa **version minimale**
     (`x-supported-versions`). La liste minimale de permissions et le budget de
     requêtes se construisent donc par lecture des spécifications, puis se confirment
     en recette ;
   - un rôle intégré **« Backup Admin »** existe : il est autorisé à lire les tâches.
     Reste à vérifier, dans les spécifications Data Protection, Volumes et VMM, qu'il
     couvre toutes les opérations du plugin ;
   - limites relevées : lecture d'une tâche 27 à 72 requêtes/s selon la taille de PC,
     mais **liste des tâches 6 requêtes/s** et liste des catégories 20 requêtes/s.
     → Le suivi se fait par lecture unitaire de la tâche attendue, jamais par listage ;
     l'intervalle de sondage croît progressivement ;
   - une modification sans `If-Match` est rejetée en HTTP 428 ;
   - états de tâche : `QUEUED`, `RUNNING`, `CANCELING`, `SUCCEEDED`, `FAILED`,
     `CANCELED`, `SUSPENDED` ; toute autre valeur est traitée comme un échec ;
   - l'API de tâches et de catégories existe depuis PC 2024.3.
   - le client confirme que ces versions (Prism v4.4, Monitoring v4.3) sont celles de
     son installation. Le plancher AOS 7.0 / pc.2024.3 est donc largement dépassé et
     le risque « cluster sous le plancher » (§ 14) est levé. La restauration par
     clonage d'un disque de Volume Group (vmm ≥ 4.1) est très probablement disponible ;
     la spécification VMM le confirmera.
   Spécifications encore attendues : § 16, point 13.

### 4.0 Versions d'API réellement servies (mesuré sur l'installation)

Test effectué sur le Prism Central du client (pc.7.5.0.6) : pour `dataprotection`,
`volumes`, `vmm`, `clustermgmt`, `networking` et `prism`, les versions **v4.0, v4.1
et v4.2 répondent ; v4.3 et v4.4 n'existent pas** (404). Pour `security`, v4.0 et
v4.1 existent, v4.2 non.

Conséquences :

- Les spécifications fournies (v4.3/v4.4) sont **plus récentes que l'installation**.
  Tout ce qui est marqué [S] ci-dessous reste une bonne indication, mais les rôles,
  limites de débit et tailles maximales peuvent différer en v4.2 : à confirmer en
  recette, ou par relecture des spécifications v4.2 si le client les fournit.
- **Règle de versions du plugin** : appeler la version la plus basse qui offre la
  fonction voulue. Soit **v4.0 partout**, sauf **VMM en v4.1** pour le clonage d'un
  disque de Volume Group vers un disque de VM (`VolumeDiskReference`, apparu en v4.1).
  Le plugin reste ainsi compatible avec les installations plus anciennes comme plus
  récentes, ce qui compte pour le reversement à la communauté.
- **Contrôle au démarrage** : le plugin vérifie que chaque version d'API qu'il utilise
  répond, et échoue sinon avec un message nommant l'API et la version manquantes.
  Les versions sont centralisées dans un seul endroit du code.
- Le chemin technique complet (points de récupération, régions modifiées, Volume
  Groups, clonage vers une VM) est donc disponible sur l'installation.

### 4.1 Faits confirmés par les spécifications Data Protection, Volumes, VMM et IAM [S]

**Le chemin technique complet existe dans les versions installées.**

| Étape | Opération | Confirmé |
|---|---|---|
| Point de récupération | création, lecture, suppression, expiration | oui, 32 entités au plus par point |
| Régions modifiées | `discover-cluster` puis `compute-changed-regions` | oui ; exige AOS (PE) ≥ 7.0 |
| Lecture | VG `TEMPORARY`, disque créé depuis `DISK_RECOVERY_POINT`, `attach-vm` | oui |
| Restauration | disque de VM cloné depuis un disque de VG (`VolumeDiskReference`) | oui en VMM v4.3 : la variante de repli de D6 (vmm v4.0) devient inutile |
| Authentification | compte de service (`SERVICE_ACCOUNT`) et clé d'API (`API_KEY`) | oui |

**Précisions sur les régions modifiées.**

- Le jeton renvoyé par `discover-cluster` se transmet au second appel dans le cookie
  de session **`NTNX_IGW_SESSION`**.
- Une réponse contient **au plus 20 000 régions** : le client doit paginer par fenêtres
  (`offset`/`length`) et ne jamais supposer qu'une réponse couvre tout le disque.
- La documentation précise que l'appel sert aussi au Full, car il signale les régions
  à zéro : cela valide la règle « un Full ne lit que les régions `REGULAR` » (D5).
- `blockSizeByte` : 32768, 65536, 131072 ou 262144. On retient 262144 par défaut
  (moins de régions, moins d'appels), paramétrable.
- `discover-cluster` est limité à **1 requête par seconde**, quelle que soit la taille
  de Prism Central : le jeton est obtenu une fois par disque puis réutilisé, jamais
  redemandé à chaque fenêtre.

**Limites de débit à respecter** (requêtes par seconde, selon la taille de PC) :
création de point 2 à 3, suppression de point 2, `set-expiration-time` 5, lecture de
point 10, opérations de VG 15 à 25, liste des VM 2 à 7, lecture d'une VM 10 à 25,
création de VM 5 à 20. La liste des VM étant la plus contrainte, la découverte se
fait **une fois par job**, en filtrant côté serveur.

**Droits : le rôle intégré « Backup Admin » couvre presque tout.** Il autorise :
créer, lire, supprimer et faire expirer les points de récupération, `discover-cluster`
et le calcul des régions modifiées ; créer et supprimer un VG, y créer un disque, lire
un VG et un disque donné, attacher et détacher le VG d'une VM ; lire, lister, créer,
modifier, éteindre et supprimer des VM, leurs disques et leurs cartes réseau ; lire
l'état de NGT ; lire une tâche.

Il **n'autorise pas** :

| Opération manquante | Conséquence | Parade |
|---|---|---|
| Lire les catégories (`View Category`) | Sélection par nom de catégorie impossible avec ce seul rôle | Rôle personnalisé = Backup Admin + `View Category` (recommandé), ou catégorie désignée par son identifiant |
| Lister les disques d'un VG et ses attachements | — | Le plugin mémorise les identifiants qu'il crée et lit chaque objet individuellement ; le balayage de nettoyage liste les VG (autorisé) par préfixe de nom |
| Associer des catégories à une VM | `restore_categories=yes` indisponible | Ajouter `Associate Virtual Machine Categories` au rôle si l'option est voulue |
| Lire les métadonnées VSS | Sans objet (Windows hors périmètre) | — |
| Créer la clé d'API | — | Clé créée une fois par un administrateur, hors du plugin |

**Conteneurs, cluster et réseaux** (Cluster Management v4.4, Networking v4.4) [S] :

| Besoin | Backup Admin | Remarque |
|---|---|---|
| Lister et lire les conteneurs de stockage | oui | API disponible à partir de **PC 7.3** |
| Espace libre d'un conteneur (statistiques) | **non** | Voir ci-dessous |
| Lire un cluster par son identifiant | oui | Identifiant pris dans la VM ou le point de récupération |
| Lister les clusters | non | Inutile : le plugin n'en a pas besoin |
| Lister les sous-réseaux | oui | 5 requêtes/s |
| Lire un sous-réseau par son identifiant | **non** | On passe par la liste filtrée, mise en cache pour le job |

- **Contrôle de capacité avant restauration.** L'espace libre (`storageFreeBytes`) est
  servi par l'opération de statistiques, hors du rôle. Deux options : ajouter
  `View Storage Container Stats` au rôle personnalisé (recommandé), ou s'en passer et
  laisser Prism refuser la création si le conteneur est plein — le plugin nettoie
  alors et échoue proprement. Le contrôle préalable devient « au mieux » dans ce cas.

**Rôle recommandé : un rôle personnalisé plutôt que « Backup Admin » tel quel.**
Backup Admin est à la fois trop étroit (catégories, statistiques) et **trop large** :
il autorise par exemple la modification du cluster et des conteneurs de stockage,
dont le plugin n'a aucun besoin. Le rôle cible contient uniquement les opérations
réellement appelées ; sa liste exacte, par opération, est un livrable de l'étape 0.
En attendant, Backup Admin + `View Category` suffit pour la recette.

La liste finale est confirmée en recette.

**Points de vigilance nouveaux.**

- Un point de récupération peut être « sécurisé » (`isSecure`) : sa suppression
  manuelle exige alors une approbation. La spécification Security v4.2 rattache cette
  fonction (« Secure Snap ») à des **politiques d'approbation associées à des
  politiques de protection** [S] : un point créé à la demande par le plugin ne devrait
  donc pas être concerné, sauf si les VM sont aussi couvertes par une politique
  sécurisée. **Vérifié sur l'installation : la fonction n'est pas activée** (l'API
  répond « policy engine is not enabled »). Le plugin contrôle tout de même ce champ
  après création et échoue avec un message explicite s'il vaut vrai, pour le jour où
  elle le serait.
- VMM accepte aussi un disque de VM cloné **directement depuis un point de disque**
  (`VmDiskRecoveryPointReference`). Hors périmètre de la v1, mais cela ouvre une
  restauration quasi instantanée depuis la base conservée sur le cluster, sans passer
  par Bareos, pour un incident survenu depuis la dernière sauvegarde.
- Les spécifications fournies pour Storage (v4.0.a3) sont une version alpha sans
  indication de rôles ni de limites : on ne s'appuie pas dessus.

## 5. Décisions de conception

### D1. Accès aux données : Volume Group temporaire attaché à la VM proxy

| Méthode | Avantages | Limites | Verdict |
|---|---|---|---|
| VG temporaire depuis le point de disque, `attach-vm` sur la proxy | Chemin prévu par l'API ; pas d'initiateur iSCSI ni de Data Services IP ; lecture locale comme périphérique bloc ; même mécanisme en sauvegarde et en restauration | La proxy doit être sur le même cluster ; nombre de disques attachables borné | **Retenu** |
| VG + iSCSI (`attach-iscsi-client`) | Proxy possible hors cluster | Data Services IP requise [?], `iscsid`, CHAP, réseau de stockage à ouvrir | Repli, option `transport=iscsi`, livré seulement si besoin avéré |
| Clone de disque attaché directement à la proxy | Fonctionne en vmm v4.0 | Modifie la configuration de la proxy à chaque job ; nettoyage plus risqué | Écarté, sauf pour la restauration en v4.0 (D6) |
| Export par l'API Images puis téléchargement | Simple | Aucun point d'entrée « image depuis un point de disque » [N] ; double copie ; pas d'incrémentale | Écarté |
| Montage NFS du conteneur | Rapide | Non confirmé comme chemin supporté [?] | Écarté |

**Deux clusters sont rattachés au Prism Central.** L'attachement direct d'un Volume
Group à une VM impose que la proxy soit sur le même cluster que la VM sauvegardée.
Il faut donc **une VM proxy par cluster** portant des VM à sauvegarder, chacune avec
son File Daemon, et des jobs Bareos dirigés vers la proxy du bon cluster. Le plugin
vérifie au démarrage que chaque VM sélectionnée est sur le cluster de sa proxy et
refuse les autres avec un message explicite. Une restauration vers l'autre cluster
passe par la proxy de ce dernier (`vm.json` autoportant, D6).

À valider en recette : identification fiable du périphérique côté proxy. Le plugin ne
doit **jamais** déduire le périphérique de l'ordre d'apparition (`/dev/sdX`) : il
attend un lien stable (`/dev/disk/by-id/…` dérivé du numéro de série du disque de VG),
vérifie la taille attendue, et ouvre en **lecture seule** en sauvegarde.

### D2. État des incrémentales : Restore Objects + étiquetage sur le cluster

- Par VM et par job, un Restore Object contient : UUID de la VM, identifiants du point
  de récupération et de chaque point de disque, taille et identifiant de chaque disque,
  version du format, nom figé de la chaîne (D4).
- Chaque point de récupération créé porte un nom structuré
  `bareos/<director>/<client>/<job>/<jobid>/<vm_uuid>` et une `expirationTime` de
  sécurité (défaut : 3 × l'intervalle entre sauvegardes, paramétrable). Si le plugin
  disparaît, le cluster se nettoie tout seul ; si la base a expiré, on retombe sur le
  repli complet.
- Le catalogue dit *quelle est la base* ; le cluster dit *si elle existe encore*. Les
  deux sont contrôlés au début de chaque incrémentale.

### D3. Purge différée de la base (N-1)

Le plugin ne peut pas savoir, à `bEventEndBackupJob`, que le job finira réellement en
succès (l'écriture côté Storage peut encore échouer). Donc :

- en fin de job N, le plugin **conserve** le point N-1 et le point N ;
- au début du job N+1, les Restore Objects reçus désignent la base réellement
  enregistrée au catalogue ; le plugin supprime alors tous **ses** points de cette VM
  sauf cette base.

Coût : deux points par VM entre deux sauvegardes. Bénéfice : un job en échec ne casse
jamais la chaîne. L'option `prune=eager` permet de supprimer N-1 dès `bEventJobEnd`
quand le statut du job est connu comme réussi.

### D4. Nommage dans le catalogue

```
@NUTANIX-AHV/<cluster>/<nom_chaine>/<vm_uuid>/vm.json
@NUTANIX-AHV/<cluster>/<nom_chaine>/<vm_uuid>/nvram.bin        (UEFI uniquement)
@NUTANIX-AHV/<cluster>/<nom_chaine>/<vm_uuid>/disk-<adresse>-<disk_uuid>.ahvs
```

- L'UUID identifie la VM ; le nom ne sert qu'à la lisibilité dans `bconsole`.
- `<nom_chaine>` est le nom de la VM **au moment du Full** et reste figé jusqu'au Full
  suivant : un renommage de VM ne casse pas la chaîne. Le renommage est journalisé.
- `<adresse>` (par exemple `scsi0`) rend les disques reconnaissables à la restauration.

### D5. Format de flux `.ahvs` (version 1)

Un flux brut ne peut pas représenter une incrémentale. Format propre, versionné :

- en-tête : magie, version, puis JSON (taille du disque, taille de bloc, type
  `full`/`delta`, identifiant du point de disque, identifiant de la base ou vide) ;
- suite d'enregistrements `(type, offset, longueur[, données])`, type `DATA` ou `ZERO` ;
- fin : nombre d'enregistrements, octets de données, empreinte XXH3 ou SHA-256 de
  l'ensemble.

Règles : les régions `ZEROED` ne sont jamais lues, seulement décrites ; un Full ne lit
que les régions `REGULAR` ; un flux sans enregistrement de fin valide est rejeté à la
restauration. L'empreinte de fin s'ajoute à la `Signature` Bareos, elle ne la remplace pas.

### D6. Restauration

1. Les Restore Objects arrivent d'abord : le plugin vérifie que la chaîne est complète
   (chaque `delta` référence le point du flux précédent). Chaîne incomplète → échec
   explicite avant toute écriture, sauf `allow_broken_chain=yes`.
2. Contrôles préalables : capacité du conteneur cible, existence du réseau, collision
   de nom, version d'API suffisante.
3. Création d'un VG de transit, attaché à la proxy ; application du Full puis des
   deltas dans l'ordre des jobs ; les enregistrements `ZERO` passent par
   `BLKZEROOUT`/`BLKDISCARD`, pas par l'écriture de zéros.
4. À `bEventEndRestoreJob` : détachement, création de la VM avec clonage des disques
   (`VolumeDiskReference`, vmm ≥ 4.1 ; sinon disque attaché à la proxy puis
   `VmDiskReference`), puis suppression du transit.

Valeurs par défaut, toutes orientées « ne rien casser » :

| Sujet | Défaut | Option |
|---|---|---|
| VM cible | nouvelle VM `<nom>-restored-<jobid>`, nouvel UUID | `target_vm_name` |
| VM homonyme existante | échec | `existing_vm=fail\|replace` ; `replace` exige la VM éteinte et la renomme `<nom>-replaced-<date>` au lieu de la détruire |
| État | éteinte | `power_on=yes` |
| Réseau | NIC recréés, **déconnectés** | `connect_nics=yes`, `network_map=ancien:nouveau` |
| Adresses MAC | nouvelles | `keep_mac=yes` |
| Conteneur | celui d'origine | `target_container`, `container_map` |
| Catégories | non réappliquées | `restore_categories=yes` |
| Disque seul | — | `restore_to_file=yes` + `Where` : image brute creuse sur la proxy, ouverte **sans troncature** pour les deltas |
| Échec en cours de route | ressources partielles supprimées | `keep_on_failure=yes` pour diagnostic |

Le `vm.json` doit être **autoportant** (restauration possible sur un autre cluster, par
une autre proxy) : CPU, mémoire, type de machine, démarrage BIOS/UEFI et Secure Boot,
ordre de démarrage, contrôleurs et adresses de disque, NIC (réseau, VLAN, MAC), CD-ROM
(sans contenu), catégories, description, fuseau matériel. Il peut contenir des données
sensibles (cloud-init) : il n'est jamais écrit dans les journaux.

### D7. Performance : un processus lecteur, les E/S dans le cœur de Bareos

Lire les blocs dans une boucle Python appelée par le FD est lent. On reprend le modèle
Proxmox [B] : un processus auxiliaire (`ahv-dumper`) lit le périphérique par grands
blocs (`O_DIRECT`, 1 à 4 Mio), produit le flux `.ahvs` dans un tube élargi, et le plugin
remet le descripteur au cœur (`iostat_do_in_core`). Symétrique en restauration. Sa
sortie d'erreur est relayée dans le journal du job ; son code de retour décide du
statut du fichier.

### D8. Cohérence des données

`consistency=crash` par défaut. `consistency=app` demande un point
`APPLICATION_CONSISTENT` (NGT requis, scripts `pre_freeze`/`post_thaw` côté Linux [?]) ;
`consistency=app_or_crash` retombe en crash-cohérent avec un avertissement. Le niveau
réellement obtenu est écrit dans le journal et dans `vm.json`.

### D9. Dépendances

Aucun SDK Nutanix à l'exécution (lourd, hors dépôts). Client REST interne, mince,
derrière une interface `PrismClient` : uniquement `python3-requests` (dépôt BaseOS de
Rocky 9) et la bibliothèque standard. Le dumper n'a aucune dépendance.

## 6. Paramètres du plugin

```
FileSet {
  Name = "ahv-prod-lot1"
  Include {
    Options { Signature = XXH128; Compression = LZ4 }
    Plugin = "python3:module_name=bareos-fd-nutanix-ahv:config_file=/etc/bareos/ahv/prod.ini:vm_category=Backup\:lot1"
  }
}
```

- Le fichier `config_file` porte ce qui est stable : `prism_url`, `ca_file`,
  `credentials_file`, valeurs par défaut. La chaîne `Plugin` ne porte que la sélection.
- Sélection : `vm_uuid`, `vm_name`, `vm_name_regex`, `vm_category`, `exclude_name_regex`,
  `exclude_category`, `exclude_disks`. La proxy elle-même est toujours exclue.
- Garde-fous : `max_vms` (échec si la sélection dépasse ce nombre : protège d'une
  expression régulière trop large), sélection vide = **erreur** par défaut
  (`allow_empty=yes` pour l'accepter).
- Toute option inconnue est une erreur fatale (comportement Proxmox [B]). Un mot de
  passe ou une clé passés dans la chaîne `Plugin` sont **refusés**.
- Options de restauration : passées par « Plugin Options » du job de restauration.

**Granularité des jobs.** Un job par VM donne un statut, une relance et une rétention
par VM, mais 120 jobs. Un job pour tout donne un statut illisible. Recommandation : des
lots par catégorie Nutanix (10 à 15 VM), la catégorie servant d'unique point de
déclaration. Dans un job multi-VM : traitement séquentiel, l'échec d'une VM n'arrête pas
les autres (`on_vm_error=continue`), le job se termine en erreur, et un récapitulatif
par VM figure en fin de journal. Une VM qui apparaît dans un lot en cours de chaîne
reçoit ses données complètes dans le job incrémental.

## 7. Cas limites à traiter explicitement

| Situation | Comportement attendu |
|---|---|
| Base introuvable, expirée, ou réponse CBT incohérente | Données complètes pour ce disque, avertissement, `fallback_to_full=no` pour échouer à la place |
| Disque ajouté depuis la base | Données complètes pour ce disque seul |
| Disque supprimé | Absent du job ; `vm.json` fait foi à la restauration |
| Disque agrandi | Données complètes pour ce disque (CBT après redimensionnement non garanti [?]) |
| Disque déplacé de conteneur, VM migrée | Tenté en delta ; repli complet si l'API refuse |
| VM renommée | Chaîne conservée (D4), nom mis à jour au prochain Full |
| VM supprimée puis recréée sous le même nom | UUID différent : nouvelle chaîne |
| VM éteinte | Sauvegardée normalement |
| VM avec Volume Group, vGPU, vTPM, disque partagé | Avertissement nommant ce qui n'est pas couvert ; `strict=yes` pour échouer |
| Niveau Différentiel | Refusé avec message clair (il imposerait de garder indéfiniment le point du Full) |
| Deux jobs sur la même VM | Verrou par UUID de VM ; le second échoue immédiatement |
| HTTP 429, 5xx, délai dépassé | Nouvelles tentatives avec attente exponentielle bornée et aléa, même `NTNX-Request-Id` ; 4xx hors 429 : échec immédiat |
| Jeton ou session expirés pendant un long job | Renouvellement transparent |
| Tâche Prism qui ne finit pas | Délai maximal par type de tâche, puis échec et nettoyage |
| Périphérique absent ou de mauvaise taille après attachement | Échec de la VM, jamais de lecture « au hasard » |
| Limite de 30 à 32 entités par point de récupération [N] | Un point par VM, pas de point groupé |
| Proxy saturée en disques attachés | File d'attente bornée par `max_attached_disks` |

## 8. Nettoyage garanti

Critère central : rien ne reste sur le cluster, quel que soit le mode de fin.

1. **Nommage déterministe** de toute ressource créée (points, VG, disques, VM de
   transit) avec le préfixe `bareos/…` et le JobId. Le plugin ne supprime **que** ce
   qui porte son préfixe et son identité (director + client).
2. **Journal d'intention** local (`/var/lib/bareos/ahv/journal/<jobid>.json`), écrit
   *avant* chaque création et mis à jour après chaque suppression.
3. **Chemins de nettoyage** : `try/finally` autour de chaque VM, puis
   `bEventEndBackupJob`, `bEventJobEnd`, `bEventCancelCommand` ; opérations
   idempotentes (« déjà supprimé » = succès).
4. **Reprise après mort brutale** : au démarrage de chaque job, et par un utilitaire
   autonome `bareos-ahv-cleanup` (minuterie systemd), balayage des journaux orphelins
   et des ressources préfixées sans job actif. Mode `--dry-run` par défaut en usage manuel.
5. **Dernier filet** : `expirationTime` sur chaque point de récupération.
6. Un nettoyage qui échoue produit une erreur dans le job (pas un simple avertissement)
   et un élément Zabbix.

## 9. Sécurité

- Compte de service dédié, clé d'API, permissions minimales documentées (livrable étape 0).
- `credentials_file` : propriétaire `root`, mode `600` ; **le plugin refuse de démarrer**
  si le fichier est lisible par d'autres.
- TLS toujours vérifié, `ca_file` pour l'AC interne, pour Prism Central **et** l'IP du
  cluster. Pas d'option pour désactiver la vérification ; pour la recette, on fournit
  l'AC ou on épingle l'empreinte (`cert_fingerprint`).
- Secrets, jetons et `vm.json` masqués dans les messages de job et de débogage ; test
  unitaire dédié qui cherche les secrets dans toutes les sorties.
- Les sauvegardes contiennent des disques complets en clair : recommander le
  chiffrement des données Bareos (PKI côté FD) ou celui du stockage, et le documenter.
- La proxy voit les disques de toute la production : VM dédiée, durcie, sans autre
  service, accès restreint, flux réseau limités à Prism (9440), Director et Storage.
- Compatibilité SELinux en mode `enforcing` vérifiée sur Rocky 9.

## 10. Journalisation et supervision

- Par VM : début, niveau effectif (full / delta / repli et sa cause), cohérence
  obtenue, par disque taille, octets lus, pourcentage lu, débit, durée ; fin et nettoyage.
- Sévérités : `M_FATAL` si rien n'est sauvegardable, `M_ERROR` par VM en échec ou
  nettoyage raté, `M_WARNING` pour repli et éléments non couverts. Vérifier en recette
  la correspondance exacte entre sévérité et statut final du job, puisque Zabbix s'y fie.
- Le statut des jobs arrive dans Zabbix par le plugin Director du lot B (§ 17). Ce
  plugin ne voit que des valeurs **par job** ; le détail par VM vient des deux
  utilitaires ci-dessous, exécutés sur la proxy.
- Fourni pour Zabbix : `bareos-ahv-cleanup --check` (nombre d'orphelins) et
  `bareos-ahv-coverage` : **liste des VM du cluster qu'aucun job ne sélectionne** et âge
  de la dernière sauvegarde réussie par VM. Avec 120 VM, l'oubli d'une VM est le risque
  le plus probable ; ce contrôle le rend visible.

## 11. Démarche par étapes

Chaque étape est livrée, testée et validée avant la suivante.

**Préalable — Lot B.** Plugin Director pour Zabbix (§ 17), déployé et validé sur les
jobs existants avant l'étape 1.

**Étape 0 — Étude et maquettes (pas de code de production).**
Livrables : note de conception confirmant ou amendant D1 à D9 ; levée de chaque point
[?] par un essai sur la recette ; liste minimale de permissions ; mesure de débit de
lecture par VG attaché ; trois scripts jetables prouvant le chemin complet (point →
VG → lecture ; régions modifiées entre deux points ; écriture → nouvelle VM qui démarre).
*Critère de sortie : aucun [?] bloquant restant.*
Le script d'essai est fourni : `tools/etape0/ahv_etape0.py` (notice dans le même dossier).

**Étape 1 — Sauvegarde Full.** Client Prism, format `.ahvs`, dumper, `vm.json`,
nettoyage complet (§ 8), journalisation.

**Étape 2 — Restauration.** Nouvelle VM, options de D6, restauration en fichier brut.
Écrite juste après le Full : une sauvegarde non restaurée n'est pas validée.

**Étape 3 — Incrémentales.** Restore Objects, régions modifiées avec validation,
replis, purge différée, contrôle de chaîne à la restauration.

**Étape 4 — Industrialisation.** Découverte, lots, RPM signé pour Nexus
(`Requires: bareos-filedaemon-python3-plugin, python3-requests`), utilitaires de
nettoyage et de couverture, modèles Zabbix, documentation française (installation,
exploitation, procédure de restauration pas à pas, dépannage, limites connues).

**Étape 5 — Reversement.** Mise en conformité avec les conventions du dépôt Bareos
(structure `core/src/plugins/filed/python/nutanix-ahv/`, test système, documentation
`.rst`), puis proposition en amont.

## 12. Stratégie de test

- **Unitaires** (sans réseau) : faux serveur Prism simulant tâches asynchrones, 429,
  5xx, expiration de jeton, réponses CBT invalides.
- **Propriété** sur le format : pour des images et des suites de modifications
  aléatoires, `Full + deltas` restaurés = image finale, octet pour octet.
- **Injection de fautes** : une exception injectée à chaque étape de la sauvegarde et
  de la restauration ; invariant vérifié à chaque fois : zéro ressource restante dans
  le faux Prism.
- **Intégration sans Nutanix** : backend « fichiers locaux » implémentant `PrismClient`,
  pour faire tourner un vrai Bareos (Full, incrémentales, restauration, annulation) en
  intégration continue.
- **Recette** sur cluster réel (Nutanix CE acceptable pour le développement, cluster de
  recette représentatif pour la validation) selon la matrice du § 13.
- Qualité : `ruff`, vérification de types, couverture ≥ 85 % sur le client Prism, le
  format et le nettoyage.

## 13. Critères d'acceptation

1. Rocky 9 (UEFI), CentOS 7 (BIOS) et Ubuntu 16.04 (MISP) : Full, restauration en
   nouvelle VM, démarrage, service applicatif fonctionnel.
2. Identité des données : empreinte de chaque disque restauré égale à celle du point
   de récupération source (comparaison faite sur la proxy).
3. Incrémentale : sur une VM où environ 1 % des blocs a changé, moins de 5 % du disque
   est lu ; restauration Full + 5 incrémentales identique octet pour octet.
4. Repli : base supprimée à la main → le job réussit avec avertissement et données
   complètes ; la restauration qui suit est correcte.
5. Nettoyage : zéro point, VG, disque ou VM temporaire après job réussi, échoué,
   annulé depuis `bconsole`, **FD tué par `kill -9`**, et proxy redémarrée en plein job
   (ces deux derniers après passage de `bareos-ahv-cleanup`). Seule la base attendue subsiste.
6. Chaîne : un job en échec entre deux incrémentales ne casse pas la suivante.
7. Erreurs : VM introuvable, Prism injoignable, certificat invalide, identifiants
   faux, conteneur plein → statut d'erreur Bareos, alerte Zabbix, message qui nomme la
   cause, aucun secret dans les journaux.
8. Sécurité : la restauration par défaut ne modifie aucune VM existante et ne connecte
   aucun réseau ; fichier d'identifiants en mode 644 → refus de démarrer.
9. Performance : débit et durée d'un lot mesurés et consignés ; l'objectif chiffré est
   fixé à l'étape 0 à partir de la fenêtre de sauvegarde (§ 16).
10. Installation depuis Nexus sur une proxy neuve, hors ligne, en suivant uniquement
    la documentation.

## 14. Risques principaux

| Risque | Parade |
|---|---|
| Cluster sous le plancher AOS 7.0 / pc.2024.3 | Décision avant l'étape 1 ; interface `PrismClient` pour isoler un éventuel backend v3 |
| Régions modifiées peu fiables selon les versions | Fenêtres bornées, validation, repli, critère n° 2 rejoué après chaque montée de version AOS |
| Rôle « Backup Admin » incomplet (catégories) | Rôle personnalisé dérivé, voir § 4.1 ; confirmation en recette |
| Débit par VG attaché insuffisant | Mesure à l'étape 0 ; plusieurs proxys ; transport iSCSI en repli |
| Limitation de débit de Prism Central | Cadence bornée côté client, mise en cache des lectures, étalement des lots |
| Deux points par VM consomment du stockage | Suivi par `totalExclusiveUsageBytes` [N], option `prune=eager` |
| Montée de version AOS ou Bareos cassant le plugin | Test système rejoué en recette avant chaque montée de version de production |

## 15. Exigences transverses

- Python 3.9 minimum, module `python3-fd` de Bareos 25.
- Aucun accès Internet à l'exécution ni à l'installation.
- Code commenté, typé, licence AGPL v3, en-têtes de licence conformes à Bareos.
- Versionnement sémantique ; le numéro de version du format `.ahvs` est indépendant, et
  toute version du plugin sait relire les formats antérieurs.

## 16. Informations à fournir avant de commencer

1. Versions exactes d'AOS et de Prism Central ; présence de Prism Central (obligatoire en v4).
2. Version exacte de Bareos (25.1.x).
3. Accès à un cluster de recette ; possibilité d'y créer une VM proxy.
4. Compte de service Prism : clé d'API possible ? Rôle maximal acceptable ?
5. AC interne signant les certificats de Prism Central et des CVM / de l'IP du cluster.
6. Volume total des disques, taux de changement quotidien estimé, fenêtre de sauvegarde.
7. Objectifs RPO et RTO pour la sauvegarde image.
8. Des VM utilisent-elles UEFI/Secure Boot, vTPM, vGPU ou des Volume Groups ?
9. NGT est-il installé dans les VM (pour la cohérence applicative) ?
10. Des Protection Domains ou politiques de protection existent-ils déjà sur ces VM ?
11. Chiffrement des sauvegardes exigé ou non.
13. Spécifications OpenAPI Nutanix : toutes celles nécessaires sont fournies (Prism,
    Data Protection, Volumes, VMM, IAM, Cluster Management, Networking, Security).
14. Secure Snap : **non activé** (vérifié). Question close.
15. Version de Prism Central : **pc.7.5.0.6** (fournie). Elle satisfait tous les
    minimums relevés (conteneurs de stockage : PC 7.3).
16. Version d'AOS des deux clusters : **7.5.0.6, licence PRO** (fournie). Le minimum
    AOS 7.0 pour les régions modifiées est satisfait ; tous les prérequis de version
    sont donc levés. Reste à confirmer en recette qu'aucune fonction utilisée
    (points de récupération à la demande, Volume Groups) n'est restreinte par le
    niveau de licence PRO [?].
    Prism Central est en licence **Starter**. Même réserve, et premier point à lever
    à l'étape 0 : création et suppression d'un point de récupération par l'API,
    création d'un Volume Group temporaire, création d'un rôle personnalisé [?].
    Si le rôle personnalisé n'est pas permis par la licence, repli sur le rôle
    intégré « Backup Admin » et sélection des VM par nom ou par identifiant de
    catégorie (§ 4.1).
17. Les deux clusters hébergent-ils tous deux des VM à sauvegarder ?
12. Pour le lot B : serveur ou proxy Zabbix joignable depuis le Director, chiffrement
    des trappers (aucun, PSK ou certificat), et nom de l'hôte Zabbix qui représente le
    Director. Version de Zabbix : **7.4.8** (fournie).

---

## 17. Lot B : plugin Director pour Zabbix

### 17.1 Objectif

À la fin de chaque job Bareos, envoyer immédiatement à Zabbix son statut et ses
mesures, sans interroger le catalogue. Sont concernés **tous** les jobs (agents
fichiers existants, futur plugin AHV, restaurations, copies).

### 17.2 Ce que le code Bareos permet

- Le Director charge des plugins Python (`python3`), configurés par job avec
  `Dir Plugin Options`, donc factorisable dans un `JobDefs` [B].
- Événements de job : `bDirEventJobInit`, `bDirEventJobStart`, `bDirEventJobRun`,
  `bDirEventJobEnd` [B]. La classe de base mesure déjà le temps total et le temps
  d'exécution [B].
- Valeurs lisibles à la fin du job [B] : nom, JobId, type, niveau, client, statut
  global, statuts FD et SD, erreurs job et SD, octets, octets lus, fichiers, débit,
  pool, stockage, type de média, nombre de volumes, priorité.
- Le plugin **ne peut pas** lire le journal du job ni agir sur son déroulement : le
  détail par VM d'un job AHV n'est pas accessible ici (voir § 10).
- `contrib/dir-plugins` contient des plugins Prometheus, Graphite et Nagios/Icinga,
  aucun pour Zabbix [B]. Le plugin Nagios utilise une ancienne signature de l'API
  (paramètre `context`) : on part de la classe de base actuelle, pas de ce code.

**Version cible : Zabbix 7.4.8.** Conséquences :

- le modèle est livré au format d'export YAML de la branche 7.4 et testé sur 7.4.8 ;
- le paquet `zabbix-sender` doit provenir de la même branche 7.x, mis en miroir sur Nexus ;
- 7.4 n'est pas une version LTS (la LTS est 7.0) [?] : le modèle et le plugin devront
  être revalidés lors du passage à la version majeure suivante. Le plugin n'utilise
  donc que des mécanismes stables d'une version à l'autre (éléments trapper, LLD,
  fonction `nodata`).

### 17.3 Décisions de conception

**Z1. Le plugin ne doit jamais dégrader un job.** Il s'exécute dans le fil du job, au
sein du Director. Donc : toute exception est interceptée, le code de retour est
toujours `bRC_OK`, les délais réseau sont courts (3 s par défaut), et un échec d'envoi
ne produit **aucun message de job** par défaut — un avertissement pourrait faire passer
un job réussi en « OK avec avertissements » [?]. L'échec d'envoi est supervisé
autrement (Z3).

**Z2. Transport : `zabbix_sender`.** Le binaire officiel gère le protocole trapper et
le chiffrement PSK ou certificat ; le module `ssl` de Python 3.9 ne gère pas le PSK.
Deux autres transports en option, sans dépendance Python externe :

- `transport=native` : protocole trapper écrit en Python (bibliothèque standard),
  pour les installations sans chiffrement ou avec certificat ;
- `transport=api` : méthode `history.push` de l'API Zabbix, disponible depuis 7.0 [?],
  en HTTPS avec un jeton d'API. Intéressant si le port trapper (10051) n'est pas
  ouvert depuis le Director mais que l'interface web l'est ; en contrepartie, un jeton
  à protéger et à renouveler. À valider sur 7.4.8 avant de le retenir.

**Z3. File d'attente locale.** Chaque résultat est d'abord écrit de façon atomique
dans `/var/lib/bareos/zabbix-spool/`, puis envoyé ; il n'est supprimé qu'après accusé
de Zabbix. Si Zabbix est injoignable, rien n'est perdu : l'envoi est retenté au job
suivant et par une minuterie systemd (`bareos-zabbix-flush`). L'horodatage d'origine
est conservé. La file est bornée (âge et nombre) ; sa profondeur est elle-même un
élément Zabbix.

**Z4. Les jobs qui ne démarrent pas ne produisent aucun événement.** Un envoi en fin
de job ne détecte ni un Director arrêté, ni un job désactivé, ni une planification
oubliée. Le modèle Zabbix livré contient donc, pour chaque job, un déclencheur
« aucune sauvegarde réussie depuis N heures » (`nodata` sur l'horodatage du dernier
succès), N étant une macro par job.

**Z5. Découverte automatique des jobs.** Le plugin ne connaît que le job en cours. Un
petit script `bareos-zabbix-discovery` (minuterie, sur le Director) liste les jobs
activés et alimente une règle de découverte Zabbix (LLD) : chaque nouveau job Bareos
obtient ses éléments et déclencheurs sans intervention.

**Z6. Rattachement.** Par défaut, tous les éléments sont portés par l'hôte Zabbix du
Director, un jeu par job. Option `host_by=client` pour les rattacher à l'hôte Zabbix
de chaque client, avec une table de correspondance si les noms diffèrent.

### 17.4 Éléments envoyés

Clés de la forme `bareos.job.<mesure>[<nom_du_job>]` :

| Mesure | Contenu |
|---|---|
| `status` | Code numérique : 0 succès, 1 succès avec avertissements, 2 erreur, 3 annulé, 4 autre |
| `status_raw` | Lettre de statut Bareos, pour diagnostic |
| `last_success` | Horodatage, envoyé seulement en cas de succès (alimente Z4) |
| `errors`, `sd_errors` | Compteurs d'erreurs |
| `bytes`, `read_bytes`, `files` | Volumes |
| `duration`, `wait_time`, `rate` | Durée d'exécution, attente en file, débit |
| `level`, `type`, `jobid` | Contexte du dernier job |
| `pool`, `storage`, `volumes` | Destination |

La table lettre → code est établie à l'étape de recette en provoquant chaque cas
(succès, avertissement, erreur, erreur fatale, annulation) ; toute lettre inconnue
donne 4 et un déclencheur d'avertissement, jamais un faux succès.

### 17.5 Paramètres

```
JobDefs {
  Name = "DefaultJob"
  Dir Plugin Options = "python3:module_name=bareos-dir-zabbix-sender:config_file=/etc/bareos/zabbix-sender.ini"
}
```

Le fichier porte : `zabbix_server`, `zabbix_port`, `zabbix_host`, `transport`,
`timeout`, `tls_psk_identity`, `tls_psk_file` ou `tls_ca_file`/`tls_cert_file`/
`tls_key_file`, `host_by`, `job_types` (défaut : tous), `spool_dir`. Aucun secret dans
`Dir Plugin Options`. Le Director tourne sous l'utilisateur `bareos` : fichiers de
secrets en mode `600`, propriétaire `bareos`, refus de démarrer l'envoi sinon.

### 17.6 Livrables

- Paquet RPM `bareos-director-plugin-zabbix` (`Requires:
  bareos-director-python3-plugin, zabbix-sender`), installable depuis Nexus.
- Modèle Zabbix exporté (règle de découverte, prototypes d'éléments et de
  déclencheurs, élément de profondeur de file).
- Scripts `bareos-zabbix-flush` et `bareos-zabbix-discovery` avec leurs minuteries.
- Documentation française ; tests unitaires avec un faux serveur trapper.
- Proposition en amont dans `contrib/dir-plugins/zabbix/`.

### 17.7 Critères d'acceptation

1. Un job réussi, un job en erreur et un job annulé apparaissent dans Zabbix avec le
   bon code en moins de 30 secondes après leur fin.
2. Zabbix arrêté pendant trois jobs : les jobs se terminent normalement, sans
   allongement notable ni changement de statut ; les trois résultats arrivent, avec
   leur horodatage d'origine, après le retour de Zabbix.
3. Un job désactivé ou jamais planifié déclenche l'alerte « aucune sauvegarde réussie
   depuis N heures ».
4. Un job créé dans Bareos apparaît dans Zabbix sans action manuelle.
5. Une erreur interne du plugin (fichier de configuration absent, binaire manquant)
   ne modifie le statut d'aucun job et se voit dans Zabbix par la profondeur de file
   ou l'absence de données.
6. Aucun secret dans la configuration Bareos affichable (`show jobs`) ni dans les journaux.

---

## 18. Lot C : ReaR avec Bareos (sans développement)

### 18.1 Principe

ReaR, installé dans une VM, produit une ISO de secours amorçable décrivant le système
(partitions, LVM, systèmes de fichiers, chargeur de démarrage) et embarquant le client
Bareos. Les données restent dans les sauvegardes fichiers existantes. Restauration :
VM vide démarrée sur l'ISO, `rear recover`, Bareos restaure les fichiers, ReaR
réinstalle le chargeur de démarrage.

### 18.2 Rôle des deux solutions

| | ReaR (lot C) | Plugin AHV (lot A) |
|---|---|---|
| Disponibilité | immédiate | après développement |
| Dépendance à Nutanix | aucune : restaure aussi ailleurs | forte |
| Dans la VM | paquet ReaR + agent Bareos | rien |
| Vitesse de restauration | plus lente (fichier par fichier) | recopie de disques |
| Usage visé | filet de sécurité général, y compris si le cluster ou le plugin fait défaut | restauration rapide et uniforme |

Les deux reposent sur des mécanismes indépendants : une panne ou un défaut de l'un
n'affecte pas l'autre.

### 18.3 Démarche

1. **Essai sur une VM Rocky 9** : installation, génération de l'ISO, restauration
   complète dans une VM vide, **mesure du temps total**. Ce chiffre sert de référence
   pour juger le gain du plugin (§ 13, critère 9).
2. Même essai sur CentOS 7 et Ubuntu 16.04, dont les dépôts fournissent d'anciennes
   versions de ReaR [?].
3. Si concluant : déploiement par lots (paquet et configuration via Nexus et l'outil
   de déploiement en place), en commençant par les VM les plus critiques.

### 18.4 Points à vérifier

- Compatibilité de la méthode Bareos de ReaR avec Bareos 25.1 [?].
- Stockage et régénération des ISO : à refaire après tout changement de partitions
  ou de noyau ; prévoir une tâche planifiée et un emplacement hors de la VM.
- Démarrage UEFI des VM restaurées.
- Supervision : âge de la dernière ISO par VM, remonté dans Zabbix.

### 18.5 Critères d'acceptation

1. Une VM Rocky 9, une CentOS 7 et une Ubuntu 16.04 sont restaurées dans une VM vide
   et démarrent, service applicatif fonctionnel.
2. Le temps de restauration est mesuré et consigné pour chacune.
3. La procédure de restauration est écrite et rejouée par une personne qui ne l'a
   pas rédigée.

---

## 19. Conformité au guide ANSSI « Sauvegarde des systèmes d'information » (v1.1, ANSSI-BP-100)

Les numéros R1 à R32 sont ceux du guide. Seules les recommandations qui touchent la
conception sont reprises ; les autres (organisation, externalisation, mise au rebut)
relèvent de l'exploitation.

### 19.1 Ce que le guide confirme

- **L'approche hybride est celle que le guide décrit** (§ 3.4) : « sauvegarder à la
  fois la machine virtuelle entièrement (fréquence faible) et les données au sein de
  la machine virtuelle (fréquence plus forte) ». Image pour reconstruire, agent
  fichiers pour les données.
- **Séparer disques « figés » et disques « vivants »** (§ 3.4, tableau 1) : le guide
  cite l'optimisation qui consiste à distinguer système et applications d'un côté,
  données et journaux de l'autre. C'est l'usage prévu de `exclude_disks`, et la
  réponse au volume des VM de journaux (QRadar, ELK).
- **Limites de l'image connues du guide** : redémarrage délicat de certaines
  applications (bases de données) après restauration d'image, inadaptation à la
  restauration de fichiers. Cohérent avec le § 2 (non-objectifs) et D8.
- **Surface d'attaque** : le guide range parmi les inconvénients de l'agent sa
  présence, avec privilèges élevés, sur chaque serveur. Le plugin n'ajoute rien dans
  les VM.

### 19.2 Changements apportés à la conception

| Réf. | Exigence du guide | Conséquence |
|---|---|---|
| § 3.4 | Image à fréquence faible, données à fréquence forte | **Schéma par défaut révisé** : image du disque système hebdomadaire ou mensuelle, données par l'agent chaque jour. Les incrémentales d'image (étape 3) deviennent **optionnelles** : à décider après mesure à l'étape 0, pour les seules VM où une image quotidienne se justifie. Le stockage nécessaire baisse fortement. |
| R8 | Flux à l'initiative du serveur vers les clients | La proxy est déclarée en **client passif** (`Passive = yes`) : le Storage ouvre la connexion de données vers la proxy, et non l'inverse [B]. Aucune connexion n'est initiée de la proxy vers l'infrastructure de sauvegarde. |
| R1, § 3.4 | Serveurs de sauvegarde cloisonnés ; s'ils sont virtualisés, uniquement avec des serveurs de même sensibilité | Le Director et le Storage restent physiques, hors cluster : conforme. **La proxy est le point sensible** : c'est un client, mais elle voit tous les disques et tourne sur le cluster de production. Voir 19.3. |
| R2, R3, R6 | Réseau d'administration, VLAN dédié, filtrage strict | Proxy sur un VLAN dédié à la sauvegarde. Flux autorisés, et eux seuls : Director → proxy, Storage → proxy, proxy → Prism Central et IP du cluster (9440). |
| R5 | Authentification indépendante de l'annuaire de production | Compte de service **local** à Prism, jamais un compte d'annuaire. |
| R17 | Comptes techniques : privilèges minimaux, renouvellement régulier des secrets, automatisé si possible | Rôle personnalisé minimal (§ 4.1). **Ajout** : clé d'API avec date d'expiration, procédure de renouvellement, âge de la clé remonté dans Zabbix. Le renouvellement est fait par un outil d'administration distinct, pas par le plugin (qui n'a pas le droit de créer des clés). |
| R28 | Chiffrement et authentification mutuelle des flux | Confirme l'**absence d'option pour désactiver la vérification TLS** vers Prism (§ 9). Côté Bareos, TLS-PSK entre démons. |
| R29, R31 | Protection des sauvegardes au niveau de la production ; chiffrement si le site n'est pas sûr | Chiffrement des données Bareos à activer pour les images, qui contiennent des disques complets ; R30 : clés au coffre et sauvegardées hors ligne. |
| R10 | Journalisation centralisée des actions | Chaque action du plugin sur Prism (création, attachement, suppression, création de VM) est journalisée avec le JobId ; journaux de la proxy et du Director envoyés au collecteur existant. Les actions du compte de service sont aussi tracées par l'audit de Prism. |
| R21 | Contrôle systématique, détection de volumes incohérents | Par VM : octets lus et taux de changement comparés aux sauvegardes précédentes ; un écart brutal lève un avertissement et un élément Zabbix. Une hausse soudaine du taux de changement est aussi un signal de chiffrement malveillant. |
| R22, R23 | Tests réguliers, procédure et ordre de restauration | **Ajout** : job de test de restauration planifié (une VM tirée au sort par lot, restaurée, démarrée sans réseau, puis supprimée), résultat dans Zabbix. La documentation contient l'ordre de restauration (DNS, annuaire, puis applications). |
| R27 | Les sauvegardes peuvent contenir des implants de l'attaquant | Une image restaure tout, y compris un éventuel code malveillant. Les défauts de D6 (VM éteinte, réseau déconnecté) permettent le contrôle avant remise en service. La documentation rappelle que, après un incident de sécurité, la reconstruction depuis des sources de confiance et la restauration des seules données sont préférables. |
| R24 | Isolement d'urgence de l'infrastructure de sauvegarde | Procédure « bouton rouge » documentée : couper le VLAN de sauvegarde, arrêter les proxys, révoquer la clé d'API. |
| R26 | Sauvegarder l'infrastructure de sauvegarde | Paquets du plugin, configuration, AC et procédure de reconstruction d'une proxy conservés avec la sauvegarde du Director. L'état des chaînes est déjà dans le catalogue (D2). |
| R16, R18, R19 | Rôles d'opérateur séparés, pas de restauration par les utilisateurs, pas de restauration croisée | Profils Bareos distincts : exploitation (lancer, restaurer en nouvelle VM) et administration (`existing_vm=replace`, changement de cluster cible). |
| R11, R12 | Règle 3-2-1, copie hors ligne indispensable | L'instantané conservé sur le cluster **n'est pas une copie de sauvegarde**. Les images suivent la même politique de copie hors ligne (bande) que les autres sauvegardes. |
| R20 | Mises à jour proactives | Le plugin et ses dépendances sont suivis comme un composant de l'infrastructure de sauvegarde ; test système rejoué avant chaque montée de version (§ 14). |

### 19.3 Point ouvert : emplacement de la proxy

La proxy concentre un accès en lecture à tous les disques de la production et tourne
sur le cluster qu'elle sauvegarde. Trois niveaux de réponse, du plus simple au plus strict :

1. **Proxy durcie sur VLAN dédié** (retenu par défaut) : VM dédiée, aucun autre
   service, filtrage de 19.2, accès d'administration restreint, mise à jour suivie.
2. **Proxy éteinte hors des fenêtres de sauvegarde**, démarrée et arrêtée par le
   Director : réduit le temps d'exposition.
3. **Lecture par iSCSI depuis une machine du SI d'administration** (repli de D1) :
   aucun composant de sauvegarde sur le cluster de production, au prix de l'ouverture
   du réseau de stockage vers cette machine.

Le choix dépend de la politique de sécurité de l'entité ; à trancher avant l'étape 1.

### 19.4 Critères d'acceptation ajoutés

1. La proxy fonctionne en client passif ; un pare-feu bloquant toute connexion
   sortante de la proxy vers le Director et le Storage ne gêne pas les sauvegardes.
2. Le test de restauration planifié tourne sans intervention et remonte dans Zabbix.
3. Une clé d'API expirée produit une erreur explicite et une alerte, avant
   l'expiration grâce au suivi de son âge.
4. La procédure d'isolement d'urgence est écrite et a été jouée une fois.
