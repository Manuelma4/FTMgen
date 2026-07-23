# FTMgen — Générateur de comparatif de travaux modificatifs

Application web qui compare la **maquette numérique** (listing Excel « Pièces + Matériel »)
avec un **plan de travaux modificatifs** (PDF vectoriel) et génère un **Excel comparatif
quantité marché / quantité après FTM** par pièce, catégorie et matériel. Après l'analyse, l'interface permet
aussi de contrôler les informations contractuelles et de générer une **Fiche de Travaux
Modificative au format Word**.

## Stack

| Couche | Techno |
|---|---|
| Backend / API | FastAPI (Python 3.11, venv local `.venv`) |
| Extraction PDF | PyMuPDF — extraction **vectorielle** (texte + coordonnées), pas d'OCR |
| Comptage symboles graphiques | OpenCV — template matching, templates auto-extraits de la légende |
| Lecture Excel | openpyxl + pandas |
| Rapport | XlsxWriter |
| Fiche FTM Word | python-docx |
| Rapprochement de noms | cache manuel → exact → fuzzy (difflib) → LLM LIHA (`etc/.env`) |
| Frontend | React 19 + TypeScript + Vite (`frontend/src`) |
| Authentification | OpenID Connect (Keycloak/Moduo), session opaque HttpOnly côté serveur |
| Persistance | JSON/Word/Excel par analyse + sessions SQLite dans le volume `output/` |

## Lancer

PremiÃ¨re installation (une seule fois) :

```powershell
cd "C:\Projet WEB\FTMgen"
.\setup.cmd
```

```powershell
cd "C:\Projet WEB\FTMgen"
.\run.cmd            # serveur sur http://127.0.0.1:8060
```

Si une ancienne version occupe déjà le port, utiliser `.\run.cmd -Restart`.

Pour choisir un autre port :

```powershell
.\run.cmd 8090
```

Le terminal doit rester ouvert pendant l'utilisation. Arrêter avec `Ctrl+C`.

En environnement local (`FTM_ENVIRONMENT=local`, valeur par défaut), FTMgen utilise
un compte local afin de garder le lancement simple. En production, le conteneur force
`FTM_AUTH_REQUIRED=true` et refuse l'accès si la configuration OIDC est incomplète.

Ouvrir ensuite `http://127.0.0.1:8060`, dÃ©poser le fichier Excel dans la zone
de gauche et le PDF dans la zone de droite, puis cliquer sur
Â« GÃ©nÃ©rer le comparatif Â». Le traitement peut prendre quelques secondes.

Une fois l'analyse ouverte, le panneau « Fiche de Travaux Modificative — Word »
regroupe les objets effectivement détectés dans le PDF. Chaque pièce Excel proposée
affiche son niveau, son occupation, son nom et son numéro afin de distinguer deux
locaux homonymes. La table présente les colonnes « Quantité marché » et
« Quantité après FTM » et permet aussi
d'ajouter des lignes manuelles. « Enregistrer » génère le Word et conserve les choix ;
« Appliquer et générer Excel + Word » recalcule les deux documents à partir du même
état. Une ligne supprimée reste exclue après les sauvegardes et peut être restaurée
avec « Rétablir depuis le PDF ». Les valeurs non renseignées restent vides.

Validation technique avec les fichiers de rÃ©fÃ©rence inclus :

```powershell
.\validate.cmd
```

Le classeur contrÃ´lÃ© est crÃ©Ã© dans `output\validation_comparatif.xlsx`.

## Authentification et historique par utilisateur

FTMgen utilise OpenID Connect par discovery ; il n'accède jamais à la base de
`moduo-auth`. Le navigateur ne reçoit qu'un identifiant de session opaque dans une
cookie `HttpOnly`, `Secure` et `SameSite=Lax`. Les jetons OIDC restent dans la base
SQLite du serveur. Chaque nouvelle analyse enregistre le claim stable `sub` de son
propriétaire ; listes, corrections, PDF, Word, Excel et suppressions sont ensuite
contrôlés avec ce même identifiant.

Les analyses historiques sans propriétaire restent accessibles uniquement en mode
local. Pour les rattacher à un compte lors du déploiement, définir explicitement
`FTM_LEGACY_OWNER_SUB` avec le `sub` OIDC concerné ; leur première modification les
rattache ensuite définitivement à ce compte.

La création du client Keycloak `ftmgen` est documentée dans
[`deploy/KEYCLOAK.md`](deploy/KEYCLOAK.md).

## Conteneur et déploiement

Le déploiement cible utilise le nom `FTMgen` et l'URL canonique
`https://ftm.moduo.fr`. Le conteneur inclut FastAPI et le build React, est publié
uniquement sur `127.0.0.1:8060` et conserve `output/` dans un volume persistant.
Apache reste l'unique entrée publique et termine TLS.

Consulter [`deploy/README.md`](deploy/README.md) pour Docker Compose, DNS, Apache,
Certbot, permissions, sauvegardes et mises à jour. Le dossier local `chat` étant vide,
ces fichiers suivent les conventions vérifiables de `moduo-auth` et de l'architecture
MODUO sans prétendre recopier un déploiement inexistant.

## Format de l'Excel d'entrÃ©e

La feuille principale doit contenir les colonnes `Occupation`, `Nom de la piÃ¨ce`,
`NumÃ©ro`, `Niveau`, `CatÃ©gorie`, `MatÃ©riel` et `QuantitÃ©`. La colonne
`Code article` est optionnelle mais fortement recommandÃ©e.

Deux feuilles historiques de correspondance restent lisibles pour compatibilité :

- `Correspondance piÃ¨ces` : `PiÃ¨ce plan (PDF)` â†’ `PiÃ¨ce existante (Excel)` ;
- `Correspondance articles` : `Article plan (PDF)` â†’ `MatÃ©riel existant (Excel)`.

Pour les nouveaux traitements, les correspondances se contrôlent dans la table Word
de l'interface : elle alimente à la fois le comparatif Excel et le document Word.

Un modÃ¨le complet peut Ãªtre tÃ©lÃ©chargÃ© depuis la page d'accueil ou via
`http://127.0.0.1:8060/api/template-excel`.

Ou en ligne de commande, sans serveur :

```powershell
.\.venv\Scripts\python.exe -m app.pipeline "listing.xlsx" "plan.pdf" -o comparatif.xlsx
```

## Comment ça marche

1. **Excel** : lecture de la feuille « Pièces + Matériel » (forward-fill des cellules
   fusionnées, filtrage des lignes placeholder `(aucun équipement rattaché)`).
2. **PDF** : classification des pages par leur cartouche (AMENAGEMENT, CLOISONNEMENT,
   ELECTRICITE, LUMINAIRE, PLOMBERIE) ; détection des pièces via les étiquettes
   « XX.XX m² + nom » (2 styles gérés : cartouche maquette et étiquette projet) ;
   symboles **texte** par catalogue regex + symboles **graphiques** par vision
   (templates extraits de la légende, matching par teinte, 4 rotations, NMS
   global — validé : Vasculaire 01 = 6 PC, comptage manuel client) ; symboles
   composés déployés (POSTE = 4 PC + 1 RJ, règle `expands` du catalogue) ;
   rattachement pièce par distance **géodésique** (les murs noirs bloquent la
   propagation — un symbole dans une alcôve revient bien à sa pièce).
3. **Comparaison** : clé = (identité physique Excel, catégorie, matériel), où
   l'identité contient niveau + occupation + pièce + numéro. Un nom homonyme n'est
   jamais agrégé ni choisi automatiquement. Le périmètre « quantité marché » est limité aux
   pièces contenant réellement un objet PDF ou une relation explicite. Les pièces du
   plan absentes de la maquette sont marquées `[nouvelle pièce]`.
   Statuts : `AJOUT`, `MODIFIÉ`, `INCHANGÉ`, `NON DÉTECTÉ SUR PLAN (à vérifier)`,
   `À VALIDER (article inconnu de la maquette)`.
4. **Rapport** : classeur avec deux onglets seulement, Synthèse et Comparatif.

## Fichiers de configuration éditables

- `app/data/symbol_catalog.json` — libellés de symboles reconnus par type de plan.
- `app/data/material_map_cache.json` — correspondances article plan → matériel
  maquette (rempli par le LLM, **corrigeable à la main** ; `null` = pas d'équivalent).
  C'est l'embryon de la future base d'articles.
- `C:\Projet WEB\etc\.env` — clés LIHA (`LIHA_CHAT_*`) ; options :
  `FTM_USE_LLM=false` pour désactiver le LLM, `FTM_LLM_TIMEOUT` (défaut 600 s).

## Limites connues

- Le comptage vision couvre les glyphes déclarés dans la section `glyphs` du
  catalogue (page ELECTRICITE) ; les autres pages restent au comptage texte →
  des lignes « NON DÉTECTÉ SUR PLAN » restent normales pour les équipements
  que le plan ne dessine pas (détection incendie, BAES…).
- Les zones sans étiquette « m² » (ex. Tisanerie sur la page élec) sont
  absorbées par la pièce géodésiquement la plus proche.
- Les quantités « Luminaire type N » (page LUMINAIRE) comptent les repères
  numériques — à confronter au plan AVENTIM comme l'indique le plan lui-même.
- Les pièces renommées (ex. « Consultation 3 » → « CABINET 3 ») apparaissent comme
  nouvelle pièce ; le rapprochement géométrique avant/après est prévu en v2.
- Plans **vectoriels** uniquement ; pour des plans scannés il faudra un OCR
  (aucune clé OCR dans `etc/.env` à ce jour).
