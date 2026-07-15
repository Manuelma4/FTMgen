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
  numero?: string;
  niveau?: string;
}

export interface FtmMaterialRow {
  id: string;
  room: string;
  material: string;
  comparison_room?: string;
  comparison_material?: string;
  quantity_before: string;
  quantity_after: string;
  unit_price: string;
  company_price: string;
  market_quantity?: string;
  additional_quantity?: string;
}

export interface FtmDocumentData {
  project_name: string;
  project_description: string;
  issuer: string;
  ftm_number: string;
  revision: string;
  subject: string;
  pole: string;
  lot: string;
  floor: string;
  description: string;
  categories: Record<'architect' | 'owner' | 'program' | 'regulation' | 'technical' | 'other', boolean>;
  category_other: string;
  attachments: Record<'plans' | 'summary' | 'estimate' | 'other', boolean>;
  attachment_other: string;
  recipients: Record<'owner' | 'assistant' | 'company', boolean>;
  architect_signatory: string;
  assistant_signatory: string;
  owner_signatory: string;
  decision: '' | 'accepted' | 'refused';
  materials_version?: number;
  materials: FtmMaterialRow[];
}

export interface AnalysisSummary {
  job: string;
  output?: string;
  download?: string;
  word_output?: string;
  word_download?: string;
  ftm_document?: FtmDocumentData;
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
