# -*- coding: utf-8 -*-
"""Comptage des symboles GRAPHIQUES par vision (template matching OpenCV).

Principe :
- les templates sont extraits automatiquement de la LÉGENDE de la page
  (le glyphe est le composant coloré connexe à gauche du texte de légende) ;
- matching par masque de teinte (les symboles élec sont colorés), 4 rotations,
  seuil adaptatif selon la finesse du trait, NMS global inter-templates ;
- rattachement des détections aux pièces par distance GÉODÉSIQUE : BFS
  multi-source depuis les étiquettes de pièces sur la carte des pixels
  praticables (les murs noirs bloquent la propagation).

Validé sur le plan 24-031 p.5 : Vasculaire 01 = 6 PC (comptage manuel client).
"""
import difflib
import math
from collections import deque

import fitz
import numpy as np

try:
    import cv2
    CV_AVAILABLE = True
except ImportError:          # dégradation gracieuse : comptage texte seul
    CV_AVAILABLE = False

ZOOM = 3.0
BFS_SCALE = 0.25
HUE_TOL = 14
MIN_PLAN_X_PT = 340          # colonne gauche (légende + notes) hors plan


def _hue_mask(hsv_img, h_med, tol=HUE_TOL):
    h = hsv_img[..., 0].astype(int)
    dh = np.minimum(np.abs(h - int(h_med)), 180 - np.abs(h - int(h_med)))
    return ((dh <= tol) & (hsv_img[..., 1] > 60) & (hsv_img[..., 2] > 60)).astype(np.uint8) * 255


class PageCV:
    """Contexte vision d'une page : rendu, carte géodésique, détection de glyphes."""

    def __init__(self, page, rooms):
        self.page = page
        self.rooms = rooms
        self.rot = page.rotation_matrix
        self.inv = ~page.rotation_matrix
        pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        self.img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_RGBA2BGR)
        self.hsv = cv2.cvtColor(self.img, cv2.COLOR_BGR2HSV)
        self._build_geodesic_map()

    # ---------- coordonnées ----------
    def to_px(self, x_pt, y_pt):
        p = fitz.Point(x_pt, y_pt) * self.rot
        return p.x * ZOOM, p.y * ZOOM

    def to_pt(self, cx, cy):
        p = fitz.Point(cx / ZOOM, cy / ZOOM) * self.inv
        return p.x, p.y

    # ---------- rattachement géodésique ----------
    def _build_geodesic_map(self):
        small = cv2.resize(self.img, None, fx=BFS_SCALE, fy=BFS_SCALE, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        free = gray > 100                      # murs/traits noirs = infranchissables
        hs, ws = free.shape
        label_map = np.full((hs, ws), -1, dtype=np.int16)
        dq = deque()
        self._seed_cells = {}
        for idx, r in enumerate(self.rooms):
            px, py = self.to_px(r.x, r.y)
            sx, sy = int(px * BFS_SCALE), int(py * BFS_SCALE)
            seed = self._nearest_cell(free, label_map, sx, sy, radius=15, need_unlabeled=True)
            if seed:
                self._seed_cells[idx] = seed
                label_map[seed[1], seed[0]] = idx
                dq.append(seed)
        while dq:
            x, y = dq.popleft()
            lbl = label_map[y, x]
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < ws and 0 <= ny < hs and free[ny, nx] and label_map[ny, nx] == -1:
                    label_map[ny, nx] = lbl
                    dq.append((nx, ny))
        self._free, self._label_map = free, label_map

    def room_polygons_pt(self, max_points=18):
        """Retourne des polygones fermés approximant les limites des pièces.

        La source est la carte géodésique : les murs noirs bloquent la
        propagation, puis le contour de chaque zone attribuée à une salle est
        simplifié. Les polygones restent des propositions de contrôle.
        """
        polygons = {}
        if not hasattr(self, "_label_map"):
            return polygons
        for idx, room in enumerate(self.rooms):
            mask = (self._label_map == idx).astype(np.uint8) * 255
            if int(mask.sum()) < 50:
                continue
            kernel = np.ones((3, 3), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            seed = self._seed_cells.get(idx)
            if seed:
                sx, sy = seed
                containing = [c for c in contours if cv2.pointPolygonTest(c, (sx, sy), False) >= 0]
                contour = max(containing or contours, key=cv2.contourArea)
            else:
                contour = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(contour)
            if area < 20:
                continue
            eps = max(1.5, 0.012 * cv2.arcLength(contour, True))
            approx = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
            if len(approx) > max_points:
                step = max(1, int(np.ceil(len(approx) / max_points)))
                approx = approx[::step]
            if len(approx) < 3:
                continue
            points = []
            for sx, sy in approx:
                px, py = float(sx) / BFS_SCALE, float(sy) / BFS_SCALE
                x_pt, y_pt = self.to_pt(px, py)
                points.append({"x": round(float(x_pt), 1), "y": round(float(y_pt), 1)})
            polygons[room.name] = points
        return polygons

    @staticmethod
    def _nearest_cell(free, label_map, sx, sy, radius, need_unlabeled=False):
        hs, ws = free.shape
        for rad in range(radius):
            for dy in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    nx, ny = sx + dx, sy + dy
                    if 0 <= nx < ws and 0 <= ny < hs and free[ny, nx] \
                            and (not need_unlabeled or label_map[ny, nx] == -1):
                        return nx, ny
        return None

    def assign_room_pt(self, x_pt, y_pt):
        """Pièce d'un point (espace texte). Retourne (nom, distance_indicative)."""
        px, py = self.to_px(x_pt, y_pt)
        sx, sy = int(px * BFS_SCALE), int(py * BFS_SCALE)
        hs, ws = self._label_map.shape
        for rad in range(12):
            for dy in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    nx, ny = sx + dx, sy + dy
                    if 0 <= nx < ws and 0 <= ny < hs and self._label_map[ny, nx] >= 0:
                        r = self.rooms[self._label_map[ny, nx]]
                        return r.name, round(math.hypot(r.x - x_pt, r.y - y_pt), 1)
        return "", 0.0

    # ---------- templates depuis la légende ----------
    def extract_templates(self, glyph_entries, norm_fn):
        """glyph_entries : {texte_légende_normalisé: (article, catégorie)}.
        Retourne ({article: (mask, hue, seuil, catégorie)}, bbox_légende)."""
        templates, boxes = {}, []
        for block in self.page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for s in line["spans"]:
                    text = s["text"].strip()
                    key = norm_fn(text).replace("º", "°")
                    if s["size"] < 8:
                        continue
                    if key not in glyph_entries:
                        # les légendes contiennent parfois des coquilles
                        # (ex. « Dowlight » pour « Downlight ») : rapprochement flou
                        if len(key) < 8:
                            continue
                        close = difflib.get_close_matches(key, list(glyph_entries), n=1, cutoff=0.84)
                        if not close:
                            continue
                        key = close[0]
                    p0 = fitz.Point(s["bbox"][0], s["bbox"][1]) * self.rot
                    p1 = fitz.Point(s["bbox"][2], s["bbox"][3]) * self.rot
                    tx0 = int(min(p0.x, p1.x) * ZOOM)
                    tx1 = int(max(p0.x, p1.x) * ZOOM)
                    ty = int((p0.y + p1.y) / 2 * ZOOM)
                    article, categorie, threshold_override, template_side, template_pick = glyph_entries[key][:5]
                    tmpl = self._glyph_right_of(tx1, ty) if template_side == "right" else self._glyph_left_of(tx0, ty, template_pick)
                    if tmpl is None and template_side == "right":
                        tmpl = self._glyph_left_of(tx0, ty, template_pick)
                    if tmpl is None:
                        continue
                    if article not in templates:
                        mask, hue, thresh, box = tmpl
                        if threshold_override is not None:
                            thresh = float(threshold_override)
                        templates[article] = (mask, hue, thresh, categorie)
                        boxes.append(box + (tx1 + int(130 * ZOOM),))
        if not boxes:
            return templates, None
        legend = (min(b[0] for b in boxes) - 30, min(b[1] for b in boxes) - 30,
                  max(b[4] for b in boxes), max(b[3] for b in boxes) + 30)
        return templates, legend

    def _glyph_left_of(self, tx, ty, pick="rightmost"):
        x0, x1 = max(tx - int(58 * ZOOM), 0), max(tx - int(3 * ZOOM), 1)
        y0, y1 = max(ty - int(13 * ZOOM), 0), ty + int(13 * ZOOM)
        crop_hsv = self.hsv[y0:y1, x0:x1]
        colored = ((crop_hsv[..., 1] > 60) & (crop_hsv[..., 2] > 60)).astype(np.uint8)
        if colored.sum() < 8:
            return None
        n, labels, stats, cents = cv2.connectedComponentsWithStats(colored, connectivity=8)
        cands = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 8]
        if not cands:
            return None
        if pick == "left_symbol":
            # Dans la légende luminaire, le chiffre de référence est souvent à
            # droite du symbole. On exclut donc le composant coloré le plus à
            # droite et on garde les composants alignés à gauche du chiffre.
            rightmost_x = max(cents[i][0] for i in cands)
            members = [i for i in cands if cents[i][0] < rightmost_x - 5 * ZOOM]
            if not members:
                left = min(cands, key=lambda i: cents[i][0])
                lx, ly = cents[left]
                members = [i for i in cands
                           if abs(cents[i][0] - lx) < 22 * ZOOM and abs(cents[i][1] - ly) < 14 * ZOOM]
        else:
            best = max(cands, key=lambda i: cents[i][0])
            bx, by = cents[best]
            members = [i for i in cands
                       if abs(cents[i][0] - bx) < 18 * ZOOM and abs(cents[i][1] - by) < 14 * ZOOM]
        sel = np.isin(labels, members)
        ys, xs = np.nonzero(sel)
        pad = 2
        cy0, cy1 = max(ys.min() - pad, 0), ys.max() + pad
        cx0, cx1 = max(xs.min() - pad, 0), xs.max() + pad
        tmpl_hsv = crop_hsv[cy0:cy1, cx0:cx1]
        m = (tmpl_hsv[..., 1] > 60) & (tmpl_hsv[..., 2] > 60)
        if m.sum() < 8:
            return None
        hue = float(np.median(tmpl_hsv[..., 0][m]))
        mask = cv2.dilate(_hue_mask(tmpl_hsv, hue), np.ones((3, 3), np.uint8))
        npx = int(m.sum())
        thresh = 0.55 if npx >= 180 else (0.62 if npx >= 120 else 0.68)
        return mask, hue, thresh, (x0, y0, x1, y1)

    def _glyph_right_of(self, tx, ty):
        """Extrait un symbole placé à droite du texte de légende.

        Utilisé notamment pour la dalle LED 60x60 dont le carré avec diagonales
        est dessiné après le libellé et non à gauche comme les autres glyphes.
        """
        x0 = min(tx + int(4 * ZOOM), self.hsv.shape[1] - 1)
        x1 = min(tx + int(120 * ZOOM), self.hsv.shape[1])
        y0, y1 = max(ty - int(28 * ZOOM), 0), min(ty + int(28 * ZOOM), self.hsv.shape[0])
        if x1 <= x0 or y1 <= y0:
            return None
        crop_hsv = self.hsv[y0:y1, x0:x1]
        colored = ((crop_hsv[..., 1] > 55) & (crop_hsv[..., 2] > 55)).astype(np.uint8)
        if colored.sum() < 20:
            return None
        colored = cv2.dilate(colored, np.ones((3, 3), np.uint8))
        n, labels, stats, cents = cv2.connectedComponentsWithStats(colored, connectivity=8)
        cands = [
            i for i in range(1, n)
            if stats[i, cv2.CC_STAT_AREA] >= 18
            and stats[i, cv2.CC_STAT_WIDTH] >= 6
            and stats[i, cv2.CC_STAT_HEIGHT] >= 6
        ]
        if not cands:
            return None
        best = max(cands, key=lambda i: stats[i, cv2.CC_STAT_AREA])
        bx, by, bw, bh, _area = stats[best]
        pad = 4
        cy0, cy1 = max(by - pad, 0), min(by + bh + pad, crop_hsv.shape[0])
        cx0, cx1 = max(bx - pad, 0), min(bx + bw + pad, crop_hsv.shape[1])
        tmpl_hsv = crop_hsv[cy0:cy1, cx0:cx1]
        m = (tmpl_hsv[..., 1] > 55) & (tmpl_hsv[..., 2] > 55)
        if m.sum() < 20:
            return None
        hue = float(np.median(tmpl_hsv[..., 0][m]))
        mask = cv2.dilate(_hue_mask(tmpl_hsv, hue, tol=18), np.ones((3, 3), np.uint8))
        npx = int(m.sum())
        thresh = 0.48 if npx >= 120 else 0.56
        return mask, hue, thresh, (x0 + cx0, y0 + cy0, x0 + cx1, y0 + cy1)

    # ---------- détection ----------
    def detect(self, templates, legend_box):
        """Retourne [(article, catégorie, x_pt, y_pt, score)] après NMS global."""
        all_dets = []
        min_x = MIN_PLAN_X_PT * ZOOM
        label_cache = {}
        for article, (tmpl, hue, thresh, categorie) in templates.items():
            page_mask = cv2.dilate(_hue_mask(self.hsv, hue), np.ones((3, 3), np.uint8))
            # les aplats pleins (murs/mobilier colorés) corrèlent avec les
            # templates fins : on rejette les fenêtres bien plus denses que le
            # template lui-même.
            integral = cv2.integral((page_mask > 0).astype(np.uint8))
            key = int(hue)
            if key not in label_cache:
                label_cache[key] = cv2.connectedComponentsWithStats(page_mask, connectivity=8)
            for k in range(4):
                t = np.rot90(tmpl, k).copy()
                th_, tw_ = t.shape
                if th_ >= page_mask.shape[0] or tw_ >= page_mask.shape[1]:
                    continue
                tmpl_fill = float((t > 0).mean())
                fill_limit = min(0.95, max(0.42, 2.5 * tmpl_fill))
                res = cv2.matchTemplate(page_mask, t, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where(res >= thresh)
                for y, x in zip(ys, xs):
                    cx, cy = x + tw_ / 2, y + th_ / 2
                    if cx < min_x:
                        continue
                    if legend_box and legend_box[0] <= cx <= legend_box[2] \
                            and legend_box[1] <= cy <= legend_box[3]:
                        continue
                    win_fill = (integral[y + th_, x + tw_] - integral[y, x + tw_]
                                - integral[y + th_, x] + integral[y, x]) / (th_ * tw_)
                    if win_fill >= fill_limit:
                        continue
                    ts = max(tmpl.shape)
                    # un match posé sur un grand aplat plein (mur/mobilier coloré)
                    # n'est pas un symbole discret ; on l'écarte avant la NMS pour
                    # qu'il ne puisse pas supprimer une vraie détection voisine.
                    if self._inside_solid_blob(label_cache[key], cx, cy, ts):
                        continue
                    all_dets.append((float(res[y, x]), cx, cy, article, categorie, ts))
        all_dets.sort(reverse=True)
        kept = []
        for score, cx, cy, article, categorie, ts in all_dets:
            r = ts * 0.7
            if any((cx - kx) ** 2 + (cy - ky) ** 2 < min(r, kts * 0.7) ** 2
                   for _, kx, ky, _, _, kts in kept):
                continue
            kept.append((score, cx, cy, article, categorie, ts))
        results = []
        for score, cx, cy, article, categorie, _ts in kept:
            x_pt, y_pt = self.to_pt(cx, cy)
            results.append((article, categorie, x_pt, y_pt, round(score, 2)))
        return results

    @staticmethod
    def _inside_solid_blob(components, cx, cy, ts):
        _count, labels, stats, _cents = components
        half = int(ts)
        x0, y0 = max(int(cx) - half, 0), max(int(cy) - half, 0)
        crop = labels[y0:int(cy) + half, x0:int(cx) + half]
        values, counts = np.unique(crop[crop > 0], return_counts=True)
        if len(values) == 0:
            return False
        blob = values[int(np.argmax(counts))]
        _x, _y, w, h, area = stats[blob]
        return (w > 4 * ts or h > 4 * ts) and area > 0.5 * w * h

    def detect_spots(self, plain, arms, legend_box):
        """Détecte les spots luminaires : disques VERTS cerclés d'orange.

        Le disque est identique pour le spot fixe et le spot orientable — seuls
        les bras rectilignes d'orientation les distinguent. Le template matching
        échouait donc (bras figés dans le template, orientation variable). On
        détecte tous les disques par leur remplissage vert, stable et unique sur
        le plan, puis on classe chaque disque selon les bras trouvés autour.

        plain / arms : (article, catégorie) ou None.
        Retourne [(article, catégorie, x_pt, y_pt, confiance)].
        """
        hue = self.hsv[..., 0]
        green = ((hue >= 35) & (hue <= 90)
                 & (self.hsv[..., 1] > 60) & (self.hsv[..., 2] > 60)).astype(np.uint8)
        # la croix orange coupe le disque en quartiers : on referme avant analyse
        green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        count, _labels, stats, cents = cv2.connectedComponentsWithStats(green, connectivity=8)
        orange = _hue_mask(self.hsv, 8, tol=16)
        results = []
        min_x = MIN_PLAN_X_PT * ZOOM
        dmin, dmax = 3.2 * ZOOM, 9.5 * ZOOM          # diamètre attendu ~5.5 pt
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if not (dmin <= w <= dmax and dmin <= h <= dmax):
                continue
            if area < 0.4 * w * h or max(w, h) > 1.8 * min(w, h):
                continue
            cx, cy = float(cents[i][0]), float(cents[i][1])
            if cx < min_x:
                continue
            if legend_box and legend_box[0] <= cx <= legend_box[2] \
                    and legend_box[1] <= cy <= legend_box[3]:
                continue
            radius = max(w, h) / 2.0
            if self._ring_ratio(orange, cx, cy, radius) < 0.35:
                continue                              # pas d'anneau orange : autre objet vert
            directions = self._arm_directions(orange, cx, cy, radius)
            if arms is not None and directions >= 2:
                article, categorie = arms
                confidence = 0.9
            elif plain is not None:
                # 0 ligne : spot isolé sûr ; 1 ligne : câble ou bras unique,
                # à faire valider dans l'interface.
                article, categorie = plain
                confidence = 0.9 if directions == 0 else 0.6
            elif arms is not None:
                article, categorie = arms
                confidence = 0.6
            else:
                continue
            x_pt, y_pt = self.to_pt(cx, cy)
            results.append((article, categorie, x_pt, y_pt, round(confidence, 2)))
        return results

    @staticmethod
    def _ring_ratio(orange, cx, cy, radius):
        """Part du pourtour du disque couverte par l'anneau orange."""
        hs, ws = orange.shape
        hits = total = 0
        for k in range(24):
            angle = 2.0 * math.pi * k / 24
            px = int(cx + (radius + 1.5) * math.cos(angle))
            py = int(cy + (radius + 1.5) * math.sin(angle))
            if 0 <= px < ws and 0 <= py < hs:
                total += 1
                if orange[max(py - 1, 0):py + 2, max(px - 1, 0):px + 2].any():
                    hits += 1
        return hits / total if total else 0.0

    @staticmethod
    def _arm_directions(orange, cx, cy, radius):
        """Nombre de directions rectilignes distinctes autour d'un disque.

        Un câble qui traverse le spot donne UNE direction (segments alignés de
        part et d'autre) ; les bras d'orientation en V/X en donnent deux.
        """
        window = int(radius * 8)
        x0, y0 = max(int(cx) - window, 0), max(int(cy) - window, 0)
        x1 = min(int(cx) + window, orange.shape[1])
        y1 = min(int(cy) + window, orange.shape[0])
        crop = orange[y0:y1, x0:x1].copy()
        lcx, lcy = cx - x0, cy - y0
        cv2.circle(crop, (int(lcx), int(lcy)), int(radius + 4), 0, -1)
        lines = cv2.HoughLinesP(crop, 1, np.pi / 180, threshold=30,
                                minLineLength=int(radius * 3.5), maxLineGap=4)
        if lines is None:
            return 0
        directions = []
        for xa, ya, xb, yb in np.asarray(lines).reshape(-1, 4):
            length_sq = float((xb - xa) ** 2 + (yb - ya) ** 2)
            # les bras d'orientation sont longs et francs ; les petits segments
            # issus de la courbure d'un câble ne comptent pas comme direction.
            if length_sq < (radius * 6) ** 2:
                continue
            # distance du centre au segment
            t = max(0.0, min(1.0, ((lcx - xa) * (xb - xa) + (lcy - ya) * (yb - ya)) / length_sq))
            dist = math.hypot(lcx - (xa + t * (xb - xa)), lcy - (ya + t * (yb - ya)))
            if dist > radius * 5:
                continue
            angle = math.degrees(math.atan2(yb - ya, xb - xa)) % 180.0
            if not any(min(abs(angle - other), 180.0 - abs(angle - other)) < 25.0
                       for other in directions):
                directions.append(angle)
        return len(directions)

    def detect_triangles(self, article_categorie, legend_box):
        """Détecte les suspensions : triangle plein logé dans un cercle fin.

        Le pictogramme de légende est un triangle CREUX sans cercle alors que
        le plan dessine un triangle PLEIN entouré d'un double cercle fin — le
        template de légende ne correspond donc à rien de fiable sur le plan
        (validé : matching réduit à des courbes de câbles au hasard). Le
        triangle plein a une signature de forme stable (aire, remplissage,
        exactement 3 sommets) qu'on détecte directement, comme detect_spots
        détecte les disques verts sans dépendre du gabarit de légende.
        """
        if article_categorie is None:
            return []
        article, categorie = article_categorie
        mask = _hue_mask(self.hsv, 8, tol=16)
        count, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        min_x = MIN_PLAN_X_PT * ZOOM
        results = []
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if not (150 <= area <= 500) or not (15 <= w <= 45) or not (15 <= h <= 40):
                continue
            aspect = w / h
            if not (0.75 <= aspect <= 1.5):
                continue
            fill = area / (w * h)
            if not (0.32 <= fill <= 0.62):
                continue
            cx, cy = float(cents[i][0]), float(cents[i][1])
            if cx < min_x:
                continue
            if legend_box and legend_box[0] <= cx <= legend_box[2] \
                    and legend_box[1] <= cy <= legend_box[3]:
                continue
            component = (labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255
            contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            contour = max(contours, key=cv2.contourArea)
            perimeter = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.05 * perimeter, True)
            if len(approx) != 3:
                continue                                  # pas un triangle : lettre, câble...
            hull = cv2.convexHull(contour)
            solidity = cv2.contourArea(contour) / (cv2.contourArea(hull) or 1)
            if solidity < 0.85:
                continue
            x_pt, y_pt = self.to_pt(cx, cy)
            results.append((article, categorie, x_pt, y_pt, 0.9))
        return results

    def detect_plumbing_companions(self, templates, anchors):
        """Détecte EF/EC en noir autour de chaque évacuation EU colorée.

        Sur ce plan les attentes sont rouges dans la légende mais imprimées en
        noir dans le plan. L'évacuation EU sert d'ancre vérifiable : au maximum
        une EF et une EC sont retenues par point d'eau.
        """
        if not templates or not anchors:
            return []
        gray = cv2.cvtColor(self.img, cv2.COLOR_BGR2GRAY)
        page_mask = cv2.dilate((gray < 110).astype(np.uint8) * 255, np.ones((3, 3), np.uint8))
        results = []
        for article, (tmpl, _hue, _threshold, categorie) in templates.items():
            for anchor in anchors:
                ax, ay = self.to_px(anchor[2], anchor[3])
                best = (-1.0, None)
                for rotation in range(4):
                    candidate = np.rot90(tmpl, rotation).copy()
                    response = cv2.matchTemplate(page_mask, candidate, cv2.TM_CCOEFF_NORMED)
                    x0, x1 = max(0, int(ax - 100)), min(response.shape[1], int(ax + 100))
                    y0, y1 = max(0, int(ay - 100)), min(response.shape[0], int(ay + 100))
                    if x1 <= x0 or y1 <= y0:
                        continue
                    _, score, _, location = cv2.minMaxLoc(response[y0:y1, x0:x1])
                    if score > best[0]:
                        best = (score, (x0 + location[0] + candidate.shape[1] / 2,
                                       y0 + location[1] + candidate.shape[0] / 2))
                if best[1] is not None and best[0] >= 0.62:
                    x_pt, y_pt = self.to_pt(*best[1])
                    results.append((article, categorie, x_pt, y_pt, round(best[0], 2)))
        return results
