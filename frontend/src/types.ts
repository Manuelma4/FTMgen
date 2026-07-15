export type PageMap = Record<string, string>;

export interface LevelOption {
  value: string;
  pieces: number;
  lignes: number;
  quantite: number;
}

export interface CatalogueSymbol {
  page_type: string;
  article: string;
  categorie: string;
  reference: string;
  count: number;
}

export interface TraceItem {
  marker: string;
  reference: string;
  detection_id: string;
  page: number;
  page_type: string;
  source: string;
  label: string;
  article: string;
  categorie: string;
  original_article?: string;
  original_reference?: string;
  confidence?: number;
  room: string;
  room_dist: number;
  x: number;
  y: number;
  left?: number;
  top?: number;
  materiel_compare?: string;
  statut?: string;
  needs_review?: boolean;
  displayKind?: 'counted' | 'manual' | 'uncatalogued';
  review?: boolean;
  ignored?: boolean;
}

export interface ManualObject {
  id: string;
  page: number;
  page_type: string;
  reference: string;
  article?: string;
  categorie?: string;
  label?: string;
  x: number;
  y: number;
  room?: string;
  ignored?: boolean;
}

export interface EditedObjectPatch {
  room?: string;
  reference?: string;
  ignored?: boolean;
}

export interface Corrections {
  rooms: unknown[];
  manual_objects: ManualObject[];
  edited_objects: Record<string, EditedObjectPatch>;
  room_mappings: Record<string, string>;
  material_mappings: Record<string, string>;
  validated_articles: string[];
}

export interface CompareRow {
  piece: string;
  categorie: string;
  materiel: string;
  quantite_avant: number;
  quantite_apres: number;
  ecart: number;
  statut: string;
  pages: string;
}

export interface AnalysisSummary {
  job: string;
  output?: string;
  download?: string;
  pdf_original?: string;
  excel_name?: string;
  pdf_name?: string;
  created_at?: string;
  updated_at?: string;
  job_status?: string;
  niveau?: string | null;
  niveau_excel_selectionne?: string | null;
  pages: PageMap;
  pieces_plan: string[];
  pieces_zones: unknown[];
  corrections: Corrections;
  referentiel_excel?: {
    pieces: string[];
    materiels: string[];
  };
  pieces_rapprochees?: Array<{ plan: string; maquette: string; score: number }>;
  pieces_non_rapprochees?: string[];
  articles_rapproches?: Array<{ plan: string; maquette: string; methode: string; score: number }>;
  objets_composes?: Array<{
    article: string;
    items: Array<{ article: string; categorie: string; quantity: number }>;
  }>;
  symboles_detectes: number;
  symboles_vision: number;
  statuts: Record<string, number>;
  lignes: number;
  comparatif: CompareRow[];
  traceabilite: TraceItem[];
  catalogue_symboles: CatalogueSymbol[];
  audit_excel: Record<string, unknown>;
}

export interface HistoryItem {
  job: string;
  created_at: string;
  updated_at?: string;
  excel_name: string;
  pdf_name: string;
  niveau?: string | null;
  job_status?: string;
  symboles_detectes: number;
  lignes: number;
}

export interface MarkerResponse {
  page: number;
  width: number;
  height: number;
  counted: TraceItem[];
  uncatalogued: TraceItem[];
}
