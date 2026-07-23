# Client Keycloak pour FTMgen

FTMgen utilise OpenID Connect contre `https://auth.moduo.fr`. Le depot
`moduo-auth` ne definit actuellement que l'infrastructure Keycloak et ne fixe
pas le nom du realm applicatif. Il faut reutiliser le realm MODUO reel s'il
existe; ne pas en inventer un different uniquement pour ce guide.

Les valeurs suivantes ne sont pas des secrets:

- Client ID: `ftmgen`
- URL publique: `https://ftm.moduo.fr`
- Callback: `https://ftm.moduo.fr/api/auth/callback`
- Retour apres deconnexion: `https://ftm.moduo.fr/`
- Scopes: `openid profile email`

## 1. Creer le client

Dans la console d'administration Keycloak:

1. Selectionner le realm MODUO effectivement utilise.
2. Creer un client OpenID Connect avec le Client ID exact `ftmgen`.
3. Activer l'authentification du client (client confidentiel).
4. Activer `Standard flow` (Authorization Code).
5. Desactiver `Implicit flow` et `Direct access grants`, sauf besoin explicite
   documente ulterieurement.
6. Configurer `Root URL` et `Home URL` sur `https://ftm.moduo.fr/`.
7. Ajouter comme URI de redirection valide exacte:
   `https://ftm.moduo.fr/api/auth/callback`.
8. Ajouter comme URI de deconnexion/post-logout valide exacte:
   `https://ftm.moduo.fr/`.
9. Configurer `Web origins` sur `https://ftm.moduo.fr` si la version de
   Keycloak l'exige. FTMgen utilise normalement des appels same-origin.

Eviter les jokers `*` dans les URI de redirection. Ils permettraient de faire
sortir un code ou un utilisateur vers une URL non prevue.

## 2. Configurer FTMgen

Recuperer le secret du client dans Keycloak et le placer uniquement dans le
`.env` du serveur:

```dotenv
OIDC_ISSUER_URL=https://auth.moduo.fr/realms/<realm-reel>
OIDC_CLIENT_ID=ftmgen
OIDC_CLIENT_SECRET=<secret-du-client>
OIDC_REDIRECT_URI=https://ftm.moduo.fr/api/auth/callback
OIDC_POST_LOGOUT_REDIRECT_URI=https://ftm.moduo.fr/
OIDC_SCOPES=openid profile email
SESSION_SECRET=<valeur-aleatoire-longue-et-unique>
FTM_AUTH_REQUIRED=true
FTM_SESSION_COOKIE_SECURE=true
```

Les chevrons indiquent des valeurs a remplacer sur le serveur. Ne jamais copier
un secret reel dans ce fichier, Git, une image Docker ou une commande conservee
dans l'historique du shell.

Le `sub` OIDC est l'identifiant stable a conserver comme proprietaire de
l'historique. Le courriel et le nom servent a l'affichage, mais ne doivent pas
remplacer `sub`, car ils peuvent changer.

## 3. Flux attendu

1. Un utilisateur non connecte ouvre `https://ftm.moduo.fr`.
2. FTMgen demarre l'Authorization Code Flow vers le realm Keycloak.
3. Keycloak renvoie le navigateur sur `/api/auth/callback` avec un code court.
4. Le backend echange ce code cote serveur, cree une session securisee et ne
   transmet jamais le secret OIDC au frontend.
5. Le frontend utilise uniquement les endpoints same-origin de FTMgen.
6. La deconnexion supprime la session locale, appelle la fin de session OIDC et
   revient sur `https://ftm.moduo.fr/`.

Apache doit transmettre `X-Forwarded-Proto: https` dans son vhost TLS. Sans cet
en-tete, l'application peut calculer un callback HTTP incorrect ou refuser le
cookie de session securise.

## 4. Verification avant production

- La page de decouverte
  `https://auth.moduo.fr/realms/<realm-reel>/.well-known/openid-configuration`
  repond et son champ `issuer` correspond exactement a `OIDC_ISSUER_URL`.
- Une navigation privee redirige vers Keycloak puis revient au callback FTMgen.
- Le cookie de session est `Secure`, `HttpOnly` et `SameSite=Lax` ou plus strict.
- Deux utilisateurs distincts ne voient pas l'historique l'un de l'autre.
- La deconnexion locale et Keycloak invalide bien la session.
- Un secret de client renouvele est reporte dans `.env`, suivi d'un redemarrage
  controle du conteneur.

Ne pas rendre FTMgen public tant que le realm reel, le client, le callback et la
separation d'historique par `sub` n'ont pas ete verifies de bout en bout.
