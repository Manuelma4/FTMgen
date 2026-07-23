# Deploiement de FTMgen

FTMgen est publie sous l'URL canonique `https://ftm.moduo.fr`. Le nom Docker
du projet, du service, de l'image et du conteneur est `ftmgen`; `FTMgen` reste
le nom affiche du produit.

## Origine du patron

Le dossier local `C:\Projet WEB\chat` a ete audite le 20 juillet 2026: il est
vide et ne contient ni Dockerfile, ni Compose, ni configuration de proxy, ni
pipeline CI/CD. Il n'existe donc aucun deploiement Chat executable a recopier.

Le present patron reprend uniquement les conventions verifiables de:

- `C:\Projet WEB\moduo-auth\docker-compose.yml` et `apache/auth.conf`: service
  Docker publie sur la boucle locale et Apache comme unique entree publique;
- `C:\Projet WEB\Architecture-Moduo\architecture-applications-moduo.drawio`:
  terminaison TLS, SSO, donnees isolees, stockage persistant, secrets hors Git,
  observabilite et conteneurs remplacables.

## Architecture de deploiement

```text
Navigateur
    |
    | HTTPS https://ftm.moduo.fr
    v
Apache :443 (TLS, en-tetes, ProxyTimeout 900)
    |
    | HTTP local
    v
127.0.0.1:8060 -> conteneur ftmgen:8060
                         |
                         v
             /home/mathis/ftmgen-data/output
                         -> /app/output
```

Le build est multi-stage:

1. Node 22 execute `npm ci` et `npm run build` dans `frontend/`.
2. Python 3.11 slim installe `requirements.txt`, copie FastAPI et le build Vite,
   puis demarre Uvicorn avec l'utilisateur non-root `ftmgen` (UID/GID `10001`).

Le repertoire `/app/output` contient les imports, analyses et propriétaires au
format JSON, les classeurs Excel, les documents Word et la base SQLite des sessions
d'authentification.
Il doit donc toujours rester monte et sauvegarde.

## 1. Preparer DNS et le serveur

Creer l'enregistrement DNS `A` (et `AAAA` seulement si IPv6 est correctement
route) de `ftm.moduo.fr` vers le serveur. Attendre sa propagation avant Certbot.

Installer Docker Engine avec le plugin Compose, Apache, Certbot et les modules
Apache requis. Sur Debian/Ubuntu:

```bash
sudo a2enmod proxy
sudo a2enmod proxy_http
sudo a2enmod headers
sudo a2enmod rewrite
sudo a2enmod ssl
```

## 2. Preparer le stockage persistant

L'image utilise un utilisateur non-root fixe, UID/GID `10001`. Le bind mount
doit exister avant `docker compose up`, car Compose est configure avec
`create_host_path: false`.

```bash
sudo mkdir -p /home/mathis/ftmgen-data/output/uploads
sudo chown -R 10001:10001 /home/mathis/ftmgen-data
sudo chmod -R u=rwX,g=rX,o= /home/mathis/ftmgen-data
```

Ne pas executer ensuite le service en root pour contourner un probleme de
permissions. Corriger le proprietaire du repertoire hote.

## 3. Configurer les variables

Depuis la racine du projet sur le serveur:

```bash
cp .env.example .env
chmod 600 .env
```

Completer obligatoirement `SESSION_SECRET`, `OIDC_ISSUER_URL` et
`OIDC_CLIENT_SECRET`. Les variables LIHA sont facultatives et
`FTM_USE_LLM=false` permet le fonctionnement sans LLM.

Le fichier `.env` reel reste sur le serveur, hors Git, hors image et hors toute
sauvegarde partagee sans chiffrement. Ne jamais placer un secret dans Compose,
le Dockerfile, la configuration Apache ou la documentation.

## 4. Valider et demarrer

```bash
docker compose config
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8060/api/health
```

Le port `8060` ne doit jamais etre publie sur `0.0.0.0` ni ouvert dans le
pare-feu. Le mapping attendu est exactement `127.0.0.1:8060:8060`.

Pour consulter les journaux:

```bash
docker compose logs --tail=200 ftmgen
```

## 5. Activer Apache et TLS

```bash
sudo cp apache/ftm.conf /etc/apache2/sites-available/ftm.conf
sudo a2ensite ftm.conf
sudo apachectl configtest
sudo systemctl reload apache2
sudo certbot --apache -d ftm.moduo.fr
```

Certbot genere habituellement `/etc/apache2/sites-available/ftm-le-ssl.conf`.
Verifier dans ce vhost HTTPS:

```apache
ProxyTimeout 900
RequestHeader set X-Forwarded-Proto "https"
RequestHeader set X-Forwarded-Port "443"
```

Verifier aussi que `ProxyPass` conserve `timeout=900`, puis executer:

```bash
sudo apachectl configtest
sudo systemctl reload apache2
curl --fail https://ftm.moduo.fr/api/health
```

Le delai de 900 secondes est necessaire pour les analyses PDF/Excel et les
appels LLM longs. Une valeur inferieure provoque des erreurs proxy alors que le
traitement continue encore dans FTMgen.

## 6. Mettre a jour

Sauvegarder `/home/mathis/ftmgen-data/output`, puis:

```bash
docker compose build --pull
docker compose up -d --remove-orphans
docker compose ps
curl --fail https://ftm.moduo.fr/api/health
```

Une reconstruction/remplacement du conteneur ne supprime pas le bind mount.
Tester regulierement la restauration des analyses, fichiers et de
`auth.sqlite3`, pas seulement la creation de l'archive.

## Points de controle avant ouverture

- DNS et certificat valides pour `ftm.moduo.fr`.
- Keycloak configure selon `deploy/KEYCLOAK.md`.
- `FTM_AUTH_REQUIRED=true` et cookies securises en production.
- Aucun secret commite ni visible dans les journaux.
- `docker compose ps` affiche le service `healthy`.
- Apache est la seule entree publique et utilise `ProxyTimeout 900` ou plus.
- Le bind mount appartient a `10001:10001` et une restauration a ete testee.
