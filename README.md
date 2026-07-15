# FTMgen — Générateur de comparatif de travaux modificatifs

Application web qui compare la **maquette numérique** (listing Excel « Pièces + Matériel »)
avec un **plan de travaux modificatifs** (PDF vectoriel) et génère un **Excel comparatif
avant / après** par pièce, catégorie et matériel. Après l'analyse, l'interface permet
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
| Frontend | page HTML/JS unique (`web/index.html`) — upgradable en React |

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

Pour choisir un autre port :

```powershell
.\run.cmd 8090
```

Le terminal doit rester ouvert pendant l'utilisation. Arrêter avec `Ctrl+C`.

Ouvrir ensuite `http://127.0.0.1:8060`, dÃ©poser le fichier Excel dans la zone
de gauche et le PDF dans la zone de droite, puis cliquer sur
Â« GÃ©nÃ©rer le comparatif Â». Le traitement peut prendre quelques secondes.

Une fois l'analyse ouverte, le panneau « Fiche de Travaux Modificative — Word »
préremplit l'étage et regroupe exclusivement les objets effectivement détectés dans
le PDF. La table présente pour chacun la quantité avant (Excel) et la quantité après
(comptage direct du PDF) ; les lignes présentes uniquement dans la maquette sont
exclues. L'objet, le pôle, le lot, le descriptif, les prix et les options
administratives restent modifiables avant de cliquer sur « Enregistrer et générer le
Word ». Les correspondances PDF → Excel se corrigent directement dans cette table :
« Enregistrer » conserve le brouillon et « Appliquer et refaire l'Excel » relance le
calcul du comparatif et la génération Excel avec ces choix. Les valeurs non
renseignées restent vides dans le document.

Validation technique avec les fichiers de rÃ©fÃ©rence inclus :

```powershell
.\validate.cmd
```

Le classeur contrÃ´lÃ© est crÃ©Ã© dans `output\validation_comparatif.xlsx`.

## Format de l'Excel d'entrÃ©e

La feuille principale doit contenir les colonnes `Occupation`, `Nom de la piÃ¨ce`,
`NumÃ©ro`, `Niveau`, `CatÃ©gorie`, `MatÃ©riel` et `QuantitÃ©`. La colonne
`Code article` est optionnelle mais fortement recommandÃ©e.

Deux feuilles optionnelles rendent les rapprochements contrÃ´lables :

- `Correspondance piÃ¨ces` : `PiÃ¨ce plan (PDF)` â†’ `PiÃ¨ce existante (Excel)` ;
- `Correspondance articles` : `Article plan (PDF)` â†’ `MatÃ©riel existant (Excel)`.

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
3. **Comparaison** : clé = (pièce, catégorie, matériel). Les pièces du plan absentes
   de la maquette sont marquées `[nouvelle pièce]` (créées par les travaux).
   Statuts : `AJOUT`, `MODIFIÉ`, `INCHANGÉ`, `NON DÉTECTÉ SUR PLAN (à vérifier)`,
   `À VALIDER (article inconnu de la maquette)`.
4. **Rapport** : classeur avec onglets Synthèse, Comparatif, Écarts uniquement,
   À valider, Traçabilité plan (chaque symbole avec page + coordonnées),
   Libellés non catalogués.

## Fichiers de configuration éditables

- `app/data/symbol_catalog.json` — libellés de symboles reconnus par type de plan.
  Consulter l'onglet « Libellés non catalogués » du rapport pour l'enrichir.
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
