import { useEffect, useMemo, useRef, useState } from 'react';
import { Download, FileText, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { SearchableRelationField } from './SearchableRelationField';
import type {
  AnalysisSummary,
  ExcelPieceOption,
  ExcelScopeOption,
  FtmDocumentData,
  FtmMaterialRow,
} from '../types';

interface FtmDocumentPanelProps {
  analysis: AnalysisSummary;
  excelPieces: ExcelPieceOption[];
  excelMaterials: string[];
  onApplyDocument: (document: FtmDocumentData) => Promise<AnalysisSummary | null>;
}

const CATEGORY_OPTIONS: Array<[keyof FtmDocumentData['categories'], string]> = [
  ['architect', 'Adaptations demandées par le Maître d’Œuvre'],
  ['owner', 'Adaptations demandées par le Maître d’Ouvrage'],
  ['program', 'Changement de programme'],
  ['regulation', 'Changement de réglementation'],
  ['technical', 'Modification et optimisation technique'],
  ['other', 'Autre cas'],
];

const ATTACHMENT_OPTIONS: Array<[keyof FtmDocumentData['attachments'], string]> = [
  ['plans', 'Plans'],
  ['summary', 'Descriptif sommaire'],
  ['estimate', 'Estimation MOE'],
  ['other', 'Autres'],
];

const RECIPIENT_OPTIONS: Array<[keyof FtmDocumentData['recipients'], string]> = [
  ['owner', 'Maîtrise d’Ouvrage'],
  ['assistant', 'Assistant Maîtrise d’Ouvrage'],
  ['company', 'Entreprise'],
];

function normalizedValue(value: string): string {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function objectRelationKey(room: string, material: string): string {
  return `v1:${normalizedValue(room)}::${normalizedValue(material)}`;
}

interface ScopeChoice extends Omit<ExcelScopeOption, 'pieces'> {
  roomOptions: ExcelPieceOption[];
  materials: string[];
  backendId: boolean;
}

function scopeLabel(niveau: string, occupation: string, numero: string): string {
  return [
    niveau || 'Niveau non renseigné',
    occupation || 'Pôle non renseigné',
    numero ? `Lot ${numero}` : 'Lot non renseigné',
  ].join(' · ');
}

function sameScope(piece: ExcelPieceOption, scope: ExcelScopeOption): boolean {
  if (piece.scope_id) return piece.scope_id === scope.id;
  return normalizedValue(piece.niveau) === normalizedValue(scope.niveau)
    && normalizedValue(piece.occupation) === normalizedValue(scope.occupation)
    && normalizedValue(piece.numero) === normalizedValue(scope.numero);
}

function buildScopeChoices(
  analysis: AnalysisSummary,
  pieces: ExcelPieceOption[],
): ScopeChoice[] {
  const backendOptions = analysis.referentiel_excel?.scope_options || [];
  const comparisonMaterials = (roomIds: Set<string>) => Array.from(new Set(
    (analysis.comparatif || [])
      .filter((row) => Boolean(row.room_id) && roomIds.has(String(row.room_id)))
      .map((row) => String(row.materiel || '').trim())
      .filter(Boolean),
  )).sort((left, right) => left.localeCompare(right, 'fr', { sensitivity: 'base' }));

  if (backendOptions.length > 0) {
    return backendOptions.map((option) => {
      const scopePieces = pieces.filter((piece) => sameScope(piece, option));
      const roomIds = new Set(scopePieces.map((piece) => piece.id));
      const providedMaterials = (option.materiels || []).map(String).filter(Boolean);
      return {
        ...option,
        label: option.label || scopeLabel(option.niveau, option.occupation, option.numero),
        roomOptions: scopePieces,
        materials: providedMaterials.length > 0 ? providedMaterials : comparisonMaterials(roomIds),
        backendId: true,
      };
    });
  }

  const grouped = new Map<string, ExcelPieceOption[]>();
  for (const piece of pieces) {
    const key = [piece.niveau, piece.occupation, piece.numero].map(normalizedValue).join('::');
    grouped.set(key, [...(grouped.get(key) || []), piece]);
  }
  return Array.from(grouped.entries()).map(([key, scopePieces]) => {
    const first = scopePieces[0];
    const roomIds = new Set(scopePieces.map((piece) => piece.id));
    const materials = comparisonMaterials(roomIds);
    return {
      id: first.scope_id || `legacy-scope:${key}`,
      label: scopeLabel(first.niveau, first.occupation, first.numero),
      niveau: first.niveau,
      occupation: first.occupation,
      numero: first.numero,
      roomOptions: scopePieces,
      materials,
      backendId: Boolean(first.scope_id),
    };
  });
}

function resolveRoomId(value: string | undefined, pieces: ExcelPieceOption[]): string {
  const candidate = String(value || '').trim();
  if (!candidate) return '';
  const byId = pieces.find((item) => item.id === candidate);
  if (byId) return byId.id;
  const byLabel = pieces.filter((item) => normalizedValue(item.label) === normalizedValue(candidate));
  if (byLabel.length === 1) return byLabel[0].id;
  const byPiece = pieces.filter((item) => normalizedValue(item.piece) === normalizedValue(candidate));
  return byPiece.length === 1 ? byPiece[0].id : '';
}

function excelMaterialsForRoom(
  roomId: string,
  pieces: ExcelPieceOption[],
  fallback: string[],
): string[] {
  const room = pieces.find((item) => item.id === roomId);
  if (!room) return fallback;
  if (room.materials !== undefined) {
    return Array.from(new Set(room.materials.map((item) => item.name).filter(Boolean)));
  }
  if (room.materiels !== undefined) return room.materiels;
  return fallback;
}

function inferScopeId(
  analysis: AnalysisSummary,
  scopes: ScopeChoice[],
  saved?: FtmDocumentData,
): string {
  const explicit = String(
    saved?.excel_scope_id
    || analysis.corrections.excel_scope_id
    || analysis.excel_scope_selectionne
    || analysis.referentiel_excel?.selected_scope_id
    || '',
  ).trim();
  if (explicit && scopes.some((scope) => scope.id === explicit)) return explicit;

  const automaticScopes = scopes.filter((scope) => scope.backendId);
  if (automaticScopes.length === 0) return '';

  const assignedRooms = (saved?.materials || [])
    .map((item) => String(item.comparison_room || '').trim())
    .filter(Boolean);
  if (assignedRooms.length > 0) {
    const matchingScopes = automaticScopes.filter((scope) => (
      assignedRooms.some((room) => Boolean(resolveRoomId(room, scope.roomOptions)))
    ));
    if (matchingScopes.length === 1) return matchingScopes[0].id;
  }

  const savedPole = normalizedValue(saved?.pole || '');
  if (savedPole) {
    const exactPoleScopes = automaticScopes.filter(
      (scope) => normalizedValue(scope.occupation) === savedPole,
    );
    if (exactPoleScopes.length === 1) return exactPoleScopes[0].id;
  }
  if (automaticScopes.length === 1) return automaticScopes[0].id;
  return '';
}

function suggestedMaterials(
  analysis: AnalysisSummary,
  scope: ScopeChoice | undefined,
  includeExcluded = false,
  allowUnscoped = false,
): FtmMaterialRow[] {
  const pieceOptions = scope?.roomOptions || (allowUnscoped ? (analysis.referentiel_excel?.piece_options || []) : []);
  function resolvedRoomId(value: string | undefined): string {
    return resolveRoomId(value, pieceOptions);
  }
  const roomMappings = new Map(
    (analysis.pieces_rapprochees || []).map((item) => [
      normalizedValue(item.plan),
      resolvedRoomId(item.room_key || item.maquette),
    ]),
  );
  const objectRelations = analysis.corrections.object_relations || {};
  const excludedRelations = new Set(analysis.corrections.excluded_relations || []);
  const articleMappings = new Map(
    (analysis.articles_rapproches || []).map((item) => [normalizedValue(item.plan), item.maquette]),
  );
  const scopeExcelMaterials = scope?.materials
    || (allowUnscoped ? (analysis.referentiel_excel?.materiels || []) : []);
  const detected = new Map<string, {
    room: string;
    material: string;
    comparisonMaterial: string;
    category: string;
    quantityAfter: number;
  }>();

  for (const item of analysis.traceabilite || []) {
    if (item.ignored && !includeExcluded) continue;
    const room = String(item.room || '').trim();
    const material = String(item.article || item.original_article || item.materiel_compare || '').trim();
    const comparisonMaterial = String(item.materiel_compare || item.article || item.original_article || '').trim();
    const category = String(item.categorie || '').trim();
    if (!material) continue;
    const key = [room, material, category].map(normalizedValue).join('|');
    const current = detected.get(key);
    if (current) {
      current.quantityAfter += 1;
    } else {
      detected.set(key, { room, material, comparisonMaterial, category, quantityAfter: 1 });
    }
  }

  return Array.from(detected.values())
    .filter((item) => includeExcluded || !excludedRelations.has(objectRelationKey(item.room, item.material)))
    .sort((left, right) => (
      left.room.localeCompare(right.room, 'fr', { sensitivity: 'base' })
      || left.material.localeCompare(right.material, 'fr', { sensitivity: 'base' })
    ))
    .map((item, index) => {
      const mappingKey = objectRelationKey(item.room, item.material);
      const relation = objectRelations[mappingKey];
      const mappedRoom = resolvedRoomId(relation?.target_room_id)
        || roomMappings.get(normalizedValue(item.room))
        || resolvedRoomId(item.room)
        || '';
      const validExcelMaterials = new Set(
        excelMaterialsForRoom(mappedRoom, pieceOptions, scopeExcelMaterials).map(normalizedValue),
      );
      const articleSuggestion = articleMappings.get(normalizedValue(item.material)) || '';
      const suggestedMaterial = validExcelMaterials.has(normalizedValue(articleSuggestion))
        ? articleSuggestion
        : (validExcelMaterials.has(normalizedValue(item.comparisonMaterial)) ? item.comparisonMaterial : '');
      const relationMaterial = mappedRoom ? (relation?.target_material || '') : '';
      const mappedMaterial = relation?.is_addition
        ? ''
        : (validExcelMaterials.has(normalizedValue(relationMaterial)) ? relationMaterial : suggestedMaterial);
      const candidatePieces = new Set([
        normalizedValue(item.room),
        normalizedValue(`${item.room} [nouvelle pièce]`),
        normalizedValue(mappedRoom || ''),
      ].filter(Boolean));
      const candidateMaterials = new Set([
        normalizedValue(item.material),
        normalizedValue(item.comparisonMaterial),
      ].filter(Boolean));
      const comparison = analysis.comparatif.find((row) => (
        (row.room_id === mappedRoom || candidatePieces.has(normalizedValue(row.piece)))
        && candidateMaterials.has(normalizedValue(row.materiel))
        && (!item.category || normalizedValue(row.categorie) === normalizedValue(item.category))
      )) || analysis.comparatif.find((row) => (
        candidatePieces.has(normalizedValue(row.piece))
        && candidateMaterials.has(normalizedValue(row.materiel))
      ));

      return {
        id: `${analysis.job}-pdf-${index}`,
        mapping_key: mappingKey,
        origin: 'pdf',
        room: item.room,
        material: item.material,
        category: item.category,
        comparison_room: mappedRoom || '',
        comparison_material: mappedMaterial,
        is_addition: relation?.is_addition || false,
        quantity_before: comparison ? String(comparison.quantite_avant) : '',
        quantity_after: String(item.quantityAfter),
        unit_price: '',
        company_price: '',
      };
    });
}

function materialKeys(item: FtmMaterialRow): string[] {
  if (item.mapping_key) return [item.mapping_key];
  const room = normalizedValue(item.room);
  return [item.material, item.comparison_material || '']
    .map((material) => `${room}|${normalizedValue(material)}`)
    .filter((key) => !key.endsWith('|'));
}

function mergePdfMaterials(
  analysis: AnalysisSummary,
  existing: FtmMaterialRow[],
  scope: ScopeChoice | undefined,
  includeExcluded = false,
  allowUnscoped = false,
): FtmMaterialRow[] {
  const existingByKey = new Map<string, FtmMaterialRow>();
  for (const item of existing || []) {
    for (const key of materialKeys(item)) existingByKey.set(key, item);
  }
  const pdfRows = suggestedMaterials(analysis, scope, includeExcluded, allowUnscoped).map((item) => {
    const previous = materialKeys(item).map((key) => existingByKey.get(key)).find(Boolean);
    return {
      ...item,
      ...(previous || {}),
      id: previous?.id || item.id,
      mapping_key: item.mapping_key,
      origin: 'pdf' as const,
      room: item.room,
      material: item.material,
      category: item.category,
      comparison_room: item.comparison_room,
      comparison_material: item.comparison_material,
      is_addition: item.is_addition,
      quantity_after: item.quantity_after,
    };
  });
  const manualRows = (existing || [])
    .filter((item) => item.origin === 'manual')
    .map((item) => {
      if (!scope) return item;
      const comparisonRoom = resolveRoomId(item.comparison_room, scope.roomOptions);
      const validMaterials = new Set(
        excelMaterialsForRoom(comparisonRoom, scope.roomOptions, scope.materials).map(normalizedValue),
      );
      const comparisonMaterial = validMaterials.has(normalizedValue(item.comparison_material || ''))
        ? item.comparison_material
        : '';
      return {
        ...item,
        comparison_room: comparisonRoom,
        comparison_material: item.is_addition ? '' : comparisonMaterial,
      };
    });
  return [...pdfRows, ...manualRows];
}

function defaultDocument(
  analysis: AnalysisSummary,
  scope: ScopeChoice | undefined,
  allowUnscoped: boolean,
): FtmDocumentData {
  return {
    project_name: 'MEDIVIE 4 - HPVA',
    project_description: "Construction d'une maison médicale - Villeneuve d'Ascq - 59",
    issuer: 'MODUO',
    ftm_number: '',
    revision: '',
    subject: '',
    pole: scope?.occupation || '',
    lot: scope?.numero || '',
    floor: scope?.niveau || analysis.niveau || '',
    excel_scope_id: scope?.id || '',
    description: '',
    categories: { architect: true, owner: true, program: false, regulation: false, technical: false, other: false },
    category_other: '',
    attachments: { plans: false, summary: false, estimate: false, other: false },
    attachment_other: '',
    recipients: { owner: true, assistant: true, company: true },
    architect_signatory: 'MODUO',
    assistant_signatory: 'H.P.M.',
    owner_signatory: 'S.H.P.L.',
    decision: '',
    materials_version: 3,
    materials: suggestedMaterials(analysis, scope, false, allowUnscoped),
  };
}

function documentForAnalysis(
  analysis: AnalysisSummary,
  pieces: ExcelPieceOption[],
): FtmDocumentData {
  const saved = analysis.ftm_document;
  const scopes = buildScopeChoices(analysis, pieces);
  const scopeId = inferScopeId(analysis, scopes, saved);
  const scope = scopes.find((item) => item.id === scopeId);
  const allowUnscoped = scopes.length === 0;
  const defaults = defaultDocument(analysis, scope, allowUnscoped);
  if (!saved) return defaults;
  const document = {
    ...defaults,
    ...saved,
    categories: { ...defaults.categories, ...(saved.categories || {}) },
    attachments: { ...defaults.attachments, ...(saved.attachments || {}) },
    recipients: { ...defaults.recipients, ...(saved.recipients || {}) },
  };
  const legacyPole = normalizedValue(saved.pole || '') === normalizedValue('Zone IRM / Local technique');
  return {
    ...document,
    excel_scope_id: scopeId,
    pole: scope && (!saved.excel_scope_id || legacyPole || !document.pole) ? scope.occupation : document.pole,
    lot: scope && (!saved.excel_scope_id || !document.lot) ? scope.numero : document.lot,
    floor: scope && (!saved.excel_scope_id || !document.floor) ? scope.niveau : document.floor,
    materials_version: 3,
    materials: mergePdfMaterials(analysis, saved.materials || [], scope, false, allowUnscoped),
  };
}

export function FtmDocumentPanel({
  analysis,
  excelPieces,
  excelMaterials,
  onApplyDocument,
}: FtmDocumentPanelProps) {
  const [form, setForm] = useState<FtmDocumentData>(() => documentForAnalysis(analysis, excelPieces));
  const [relationsBusy, setRelationsBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const lastServerDocument = useRef(analysis.ftm_document);

  useEffect(() => {
    if (analysis.ftm_document === lastServerDocument.current) return;
    lastServerDocument.current = analysis.ftm_document;
    setForm(documentForAnalysis(analysis, excelPieces));
  }, [analysis, excelPieces]);

  const scopeOptions = useMemo(
    () => buildScopeChoices(analysis, excelPieces),
    [analysis, excelPieces],
  );
  const selectedScope = scopeOptions.find((item) => item.id === form.excel_scope_id);
  const roomOptions = useMemo(
    () => (selectedScope?.roomOptions || (scopeOptions.length === 0 ? excelPieces : []))
      .map((item) => ({ value: item.id, label: item.label })),
    [excelPieces, scopeOptions.length, selectedScope],
  );
  const scopedExcelMaterials = selectedScope?.materials || (scopeOptions.length === 0 ? excelMaterials : []);
  const validScopedRooms = new Set(roomOptions.map((item) => item.value));
  const scopeMissing = scopeOptions.length > 0 && !selectedScope;
  const pdfCount = form.materials.filter((item) => item.origin !== 'manual').length;
  const manualCount = form.materials.length - pdfCount;
  const unresolvedRooms = form.materials.filter((item) => (
    item.origin !== 'manual'
    && (!item.comparison_room || (Boolean(selectedScope) && !validScopedRooms.has(item.comparison_room)))
  )).length;
  const unresolvedMaterials = form.materials.filter((item) => {
    if (item.origin === 'manual' || item.is_addition) return false;
    const validMaterials = new Set(materialOptionsFor(item).map(normalizedValue));
    return !item.comparison_material || (
      Boolean(selectedScope)
      && !validMaterials.has(normalizedValue(item.comparison_material))
    );
  }).length;

  function correspondingRoom(item: FtmMaterialRow): string {
    if (selectedScope) return resolveRoomId(item.comparison_room, selectedScope.roomOptions);
    return scopeOptions.length === 0 ? (item.comparison_room || '') : '';
  }

  function materialOptionsFor(item: FtmMaterialRow): string[] {
    if (!selectedScope) return scopedExcelMaterials;
    return excelMaterialsForRoom(
      correspondingRoom(item), selectedScope.roomOptions, scopedExcelMaterials,
    );
  }

  function correspondingMaterial(item: FtmMaterialRow): string {
    if (item.is_addition) return '';
    const material = item.comparison_material || '';
    const validMaterials = new Set(materialOptionsFor(item).map(normalizedValue));
    if (selectedScope && !validMaterials.has(normalizedValue(material))) return '';
    return material;
  }

  function quantityBefore(item: FtmMaterialRow): string {
    const room = correspondingRoom(item);
    const material = correspondingMaterial(item);
    if (!room || !material) return '0';
    const roomInventory = (selectedScope?.roomOptions || excelPieces)
      .find((option) => option.id === room)?.materials;
    if (roomInventory !== undefined) {
      const matchingMaterials = roomInventory.filter(
        (entry) => normalizedValue(entry.name) === normalizedValue(material),
      );
      const matchingCategory = matchingMaterials.filter((entry) => (
        Boolean(item.category)
        && normalizedValue(entry.category) === normalizedValue(item.category || '')
      ));
      const inventoryRows = matchingCategory.length > 0 ? matchingCategory : matchingMaterials;
      if (inventoryRows.length > 0) {
        return String(inventoryRows.reduce((total, entry) => total + Number(entry.quantity || 0), 0));
      }
    }
    const comparisons = analysis.comparatif.filter((row) => (
      (row.room_id === room || normalizedValue(row.piece) === normalizedValue(room))
      && normalizedValue(row.materiel) === normalizedValue(material)
    ));
    const comparison = comparisons.find((row) => (
      Boolean(item.category)
      && normalizedValue(row.categorie) === normalizedValue(item.category || '')
    )) || comparisons[0];
    return comparison ? String(comparison.quantite_avant) : '0';
  }

  function updateField<Key extends keyof FtmDocumentData>(key: Key, value: FtmDocumentData[Key]): void {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateExcelScope(value: string): void {
    const nextScope = scopeOptions.find((scope) => scope.id === value);
    setForm((current) => {
      if (!nextScope) {
        return {
          ...current,
          excel_scope_id: '',
          pole: '',
          lot: '',
          materials: current.materials.map((item) => ({
            ...item,
            comparison_room: '',
            comparison_material: '',
          })),
        };
      }

      const visiblePdfKeys = new Set(
        current.materials
          .filter((item) => item.origin !== 'manual')
          .flatMap(materialKeys),
      );
      const recalculated = mergePdfMaterials(analysis, current.materials, nextScope);
      return {
        ...current,
        excel_scope_id: nextScope.id,
        pole: nextScope.occupation,
        lot: nextScope.numero,
        floor: nextScope.niveau || current.floor,
        materials: recalculated.filter((item) => (
          item.origin === 'manual' || materialKeys(item).some((key) => visiblePdfKeys.has(key))
        )),
      };
    });
    setError('');
    setMessage(nextScope
      ? `Périmètre Excel limité à ${nextScope.label}. Les correspondances ont été recalculées dans ce bloc ; le prochain enregistrement régénérera Excel et Word.`
      : 'Sélectionnez le pôle / lot Excel correspondant au plan PDF.');
  }

  function updateMaterialRow(id: string, patch: Partial<FtmMaterialRow>): void {
    setForm((current) => ({
      ...current,
      materials: current.materials.map((item) => item.id === id ? { ...item, ...patch } : item),
    }));
  }

  function updateComparisonRoom(source: FtmMaterialRow, value: string): void {
    const scopedValue = value && validScopedRooms.has(value) ? value : '';
    const targetMaterials = new Set(
      (scopedValue ? excelMaterialsForRoom(
        scopedValue, selectedScope?.roomOptions || [], scopedExcelMaterials,
      ) : []).map(normalizedValue),
    );
    setForm((current) => ({
      ...current,
      materials: current.materials.map((item) => (
        item.id === source.id || (
          source.origin !== 'manual'
          && item.origin !== 'manual'
          && normalizedValue(item.room) === normalizedValue(source.room)
        )
          ? {
            ...item,
            comparison_room: scopedValue,
            comparison_material: item.is_addition || targetMaterials.has(
              normalizedValue(item.comparison_material || ''),
            ) ? item.comparison_material : '',
          }
          : item
      )),
    }));
  }

  function removeMaterial(id: string): void {
    setForm((current) => ({ ...current, materials: current.materials.filter((item) => item.id !== id) }));
  }

  function addManualMaterial(): void {
    const id = globalThis.crypto?.randomUUID?.() || `manual-${Date.now()}`;
    setForm((current) => ({
      ...current,
      materials_version: 3,
      materials: [...current.materials, {
        id,
        mapping_key: `manual:${id}`,
        origin: 'manual',
        room: '',
        material: '',
        category: '',
        comparison_room: '',
        comparison_material: '',
        is_addition: true,
        quantity_before: '0',
        quantity_after: '1',
        unit_price: '',
        company_price: '',
      }],
    }));
    setMessage('Ligne manuelle ajoutée. Complétez la pièce, l’objet et la quantité.');
  }

  function restorePdfMaterials(): void {
    setForm((current) => ({
      ...current,
      materials_version: 3,
      materials: mergePdfMaterials(
        analysis,
        current.materials,
        selectedScope,
        true,
        scopeOptions.length === 0,
      ),
    }));
    setError('');
    setMessage('Liste rétablie exclusivement depuis les objets détectés dans le PDF.');
  }

  async function runRelationAction(action: () => Promise<void>): Promise<void> {
    setRelationsBusy(true);
    setError('');
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Impossible d’enregistrer les correspondances');
    } finally {
      setRelationsBusy(false);
    }
  }

  function documentPayload(): FtmDocumentData {
    return {
      ...form,
      excel_scope_id: selectedScope?.id || '',
      materials_version: 3,
      materials: form.materials.map((item) => ({
        ...item,
        mapping_key: item.mapping_key || objectRelationKey(item.room, item.material),
        comparison_room: correspondingRoom(item),
        comparison_material: correspondingMaterial(item),
        quantity_before: quantityBefore(item),
      })),
    };
  }

  async function applyAndGenerate(): Promise<void> {
    setMessage('');
    if (scopeMissing) {
      setError('Sélectionnez d’abord le pôle / lot Excel correspondant au plan PDF.');
      return;
    }
    const result = await onApplyDocument(documentPayload());
    if (!result) return;
    if (result.ftm_document) setForm(result.ftm_document);
    setMessage('Correspondances enregistrées. Excel et Word ont été régénérés avec les mêmes valeurs.');
  }

  return (
    <section className="panel ftm-panel">
      <div className="ftm-heading">
        <div>
          <span className="eyebrow">Document contractuel</span>
          <h2>Fiche de Travaux Modificative — Word</h2>
          <p>Contrôlez les champs et les matériels. Une valeur laissée vide restera vide dans le document.</p>
        </div>
        {analysis.word_download && (
          <a className="button" href={analysis.word_download}>
            <Download size={16} /> Télécharger le Word
          </a>
        )}
      </div>

      <div className="ftm-fields">
        <label>
          Projet
          <input value={form.project_name} onChange={(event) => updateField('project_name', event.target.value)} />
        </label>
        <label className="span-2">
          Opération
          <input value={form.project_description} onChange={(event) => updateField('project_description', event.target.value)} />
        </label>
        <label>
          Émetteur
          <input value={form.issuer} onChange={(event) => updateField('issuer', event.target.value)} />
        </label>
        <label>
          FTM n°
          <input value={form.ftm_number} onChange={(event) => updateField('ftm_number', event.target.value)} />
        </label>
        <label>
          Indice
          <input value={form.revision} onChange={(event) => updateField('revision', event.target.value)} />
        </label>
        <label className="span-3">
          Objet de cette Fiche de Travaux Modificative
          <input value={form.subject} onChange={(event) => updateField('subject', event.target.value)} />
        </label>
        <label>
          Pôle
          <input value={form.pole} onChange={(event) => updateField('pole', event.target.value)} />
        </label>
        <label>
          LOT
          <input value={form.lot} onChange={(event) => updateField('lot', event.target.value)} />
        </label>
        <label>
          Étage
          <input value={form.floor} onChange={(event) => updateField('floor', event.target.value)} />
        </label>
        <label className="span-3">
          Descriptif des modifications
          <textarea rows={5} value={form.description} onChange={(event) => updateField('description', event.target.value)} />
        </label>
      </div>

      <section className="ftm-materials">
        <div className="section-heading">
          <div>
            <h3>Objets et matériels</h3>
            <p>Objets détectés dans le PDF et lignes manuelles ajoutées par l’utilisateur.</p>
          </div>
          <div className="actions">
            <button type="button" className="compact-button" onClick={addManualMaterial}>
              <Plus size={16} /> Ajouter une ligne
            </button>
            <button type="button" className="compact-button" onClick={restorePdfMaterials}>
              <RefreshCw size={16} /> Rétablir depuis le PDF
            </button>
          </div>
        </div>
        <div className="word-scope-panel">
          <label htmlFor={`excel-scope-${analysis.job}`}>
            <span>Pôle / lot du référentiel Excel</span>
            <select
              id={`excel-scope-${analysis.job}`}
              value={selectedScope?.id || ''}
              onChange={(event) => updateExcelScope(event.target.value)}
            >
              <option value="">Sélectionner le bloc correspondant au PDF</option>
              {scopeOptions.map((scope) => (
                <option key={scope.id} value={scope.id}>{scope.label}</option>
              ))}
            </select>
          </label>
          {selectedScope ? (
            <div className="word-scope-summary" aria-live="polite">
              <span><strong>Niveau</strong>{selectedScope.niveau || '—'}</span>
              <span><strong>Pôle</strong>{selectedScope.occupation || '—'}</span>
              <span><strong>Lot</strong>{selectedScope.numero || '—'}</span>
              <span><strong>Pièces disponibles</strong>{selectedScope.roomOptions.length}</span>
            </div>
          ) : (
            <p className="word-scope-help">
              Choisissez d’abord le bloc Excel : seules ses pièces et ses matériels seront proposés.
            </p>
          )}
        </div>
        <div className="word-relations-toolbar">
          <div className="word-relations-copy">
            <div className="word-relations-title">
              <strong>Correspondances PDF → Excel</strong>
              <span className="word-row-count">{pdfCount} objets PDF</span>
              {manualCount > 0 && <span className="word-row-count manual-count">{manualCount} saisie{manualCount > 1 ? 's' : ''}</span>}
            </div>
            <span>Chaque pièce Excel indique niveau, occupation et numéro afin de distinguer les pièces homonymes.</span>
          </div>
          <div className="actions word-relation-actions">
            <button type="button" disabled={relationsBusy || scopeMissing} onClick={() => void runRelationAction(applyAndGenerate)}>
              <Save size={16} /> Enregistrer
            </button>
            <button type="button" className="primary" disabled={relationsBusy || scopeMissing} onClick={() => void runRelationAction(applyAndGenerate)}>
              <RefreshCw size={16} /> Appliquer et générer Excel + Word
            </button>
          </div>
        </div>
        {(unresolvedRooms > 0 || unresolvedMaterials > 0) && (
          <p className="word-relations-warning">
            À contrôler avant application : {unresolvedRooms} ligne{unresolvedRooms > 1 ? 's' : ''} sans pièce Excel
            {unresolvedMaterials > 0
              ? ` · ${unresolvedMaterials} ligne${unresolvedMaterials > 1 ? 's' : ''} sans matériel Excel validé`
              : ''}.
          </p>
        )}
        <div className="ftm-table-wrap">
          <table className="ftm-material-table" aria-label="Objets PDF et correspondances Excel">
            <thead>
              <tr className="word-table-groups">
                <th colSpan={2}>Source PDF</th>
                <th colSpan={2}>Correspondance Excel</th>
                <th colSpan={4}>Quantités et prix</th>
                <th rowSpan={2}><span className="sr-only">Actions</span></th>
              </tr>
              <tr>
                <th>Pièce PDF</th>
                <th>Objet PDF</th>
                <th>Pièce Excel</th>
                <th>Matériel Excel</th>
                <th>Quantité marché</th>
                <th>Quantité après FTM</th>
                <th>Prix unitaire</th>
                <th>Prix entreprise</th>
              </tr>
            </thead>
            <tbody>
              {form.materials.map((item) => (
                <tr key={item.id} className={item.origin === 'manual' ? 'word-manual-row' : undefined}>
                  <td className="word-source-cell">
                    {item.origin === 'manual' ? (
                      <label className="word-manual-field">
                        <span>Saisie manuelle</span>
                        <input
                          aria-label="Pièce de la ligne manuelle"
                          placeholder="Nom de la pièce"
                          value={item.room}
                          onChange={(event) => updateMaterialRow(item.id, { room: event.target.value })}
                        />
                      </label>
                    ) : <span className="word-source-value" title={item.room}>{item.room || '—'}</span>}
                  </td>
                  <td className="word-source-cell">
                    {item.origin === 'manual' ? (
                      <input
                        aria-label="Objet de la ligne manuelle"
                        placeholder="Objet ou matériel"
                        value={item.material}
                        onChange={(event) => updateMaterialRow(item.id, { material: event.target.value })}
                      />
                    ) : <span className="word-source-value word-source-material" title={item.material}>{item.material || '—'}</span>}
                  </td>
                  <td className="word-correspondence-cell">
                    <SearchableRelationField
                      value={item.comparison_room}
                      options={roomOptions}
                      disabled={scopeMissing}
                      placeholder="Rechercher niveau, occupation, pièce ou n°"
                      specialLabel="Nouvelle pièce / sans relation"
                      ariaLabel={`Pièce Excel correspondant à ${item.room || 'la saisie manuelle'}`}
                      onChange={(value) => updateComparisonRoom(item, value)}
                    />
                  </td>
                  <td className="word-correspondence-cell">
                    <SearchableRelationField
                      value={item.is_addition ? '' : item.comparison_material}
                      options={materialOptionsFor(item)}
                      disabled={scopeMissing || Boolean(item.is_addition)}
                      placeholder="Rechercher un matériel Excel"
                      specialLabel={item.is_addition ? 'Validé comme ajout' : 'Sans correspondance Excel'}
                      ariaLabel={`Matériel Excel correspondant à ${item.material || 'la saisie manuelle'}`}
                      onChange={(value) => updateMaterialRow(item.id, {
                        comparison_material: value,
                        is_addition: value === '',
                      })}
                    />
                    <label className="check-row word-addition-check">
                      <input
                        type="checkbox"
                        checked={Boolean(item.is_addition)}
                        onChange={(event) => updateMaterialRow(item.id, {
                          is_addition: event.target.checked,
                          comparison_material: event.target.checked ? '' : item.comparison_material,
                        })}
                      />
                      Ajout sans équivalent Excel
                    </label>
                  </td>
                  <td className="word-quantity-cell"><output className="word-quantity word-quantity-before" aria-label="Quantité marché">{quantityBefore(item)}</output></td>
                  <td className="word-quantity-cell">
                    {item.origin === 'manual' ? (
                      <input
                        className="word-manual-quantity"
                        aria-label="Quantité après FTM de la ligne manuelle"
                        inputMode="numeric"
                        value={item.quantity_after}
                        onChange={(event) => updateMaterialRow(item.id, { quantity_after: event.target.value })}
                      />
                    ) : <output className="word-quantity word-quantity-after" aria-label="Quantité après FTM">{item.quantity_after}</output>}
                  </td>
                  <td><input aria-label="Prix unitaire" inputMode="decimal" value={item.unit_price} onChange={(event) => updateMaterialRow(item.id, { unit_price: event.target.value })} /></td>
                  <td><input aria-label="Prix entreprise" inputMode="decimal" value={item.company_price} onChange={(event) => updateMaterialRow(item.id, { company_price: event.target.value })} /></td>
                  <td>
                    <button type="button" className="icon-button danger" aria-label={`Supprimer ${item.material || 'la ligne'}`} onClick={() => removeMaterial(item.id)}>
                      <Trash2 size={16} />
                    </button>
                  </td>
                </tr>
              ))}
              {form.materials.length === 0 && (
                <tr><td className="empty-table" colSpan={9}>Aucun objet détecté dans le PDF.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {(analysis.objets_composes || []).length > 0 && (
          <details className="component-rules">
            <summary>Objets composés</summary>
            {(analysis.objets_composes || []).map((rule) => (
              <div className="component-rule" key={rule.article}>
                <strong>{rule.article}</strong>
                <span>{rule.items.map((entry) => `${entry.quantity} × ${entry.article}`).join(' · ')}</span>
              </div>
            ))}
          </details>
        )}
      </section>

      <div className="ftm-options-grid">
        <fieldset>
          <legend>Catégorie de la demande</legend>
          {CATEGORY_OPTIONS.map(([key, label]) => (
            <label className="check-row" key={key}>
              <input
                type="checkbox"
                checked={form.categories[key]}
                onChange={(event) => updateField('categories', { ...form.categories, [key]: event.target.checked })}
              />
              {label}
            </label>
          ))}
          {form.categories.other && <input aria-label="Précision autre catégorie" placeholder="Préciser" value={form.category_other} onChange={(event) => updateField('category_other', event.target.value)} />}
        </fieldset>
        <fieldset>
          <legend>Documents MOE joints</legend>
          {ATTACHMENT_OPTIONS.map(([key, label]) => (
            <label className="check-row" key={key}>
              <input
                type="checkbox"
                checked={form.attachments[key]}
                onChange={(event) => updateField('attachments', { ...form.attachments, [key]: event.target.checked })}
              />
              {label}
            </label>
          ))}
          {form.attachments.other && <input aria-label="Précision autre document" placeholder="Préciser" value={form.attachment_other} onChange={(event) => updateField('attachment_other', event.target.value)} />}
        </fieldset>
        <fieldset>
          <legend>Diffusion</legend>
          {RECIPIENT_OPTIONS.map(([key, label]) => (
            <label className="check-row" key={key}>
              <input
                type="checkbox"
                checked={form.recipients[key]}
                onChange={(event) => updateField('recipients', { ...form.recipients, [key]: event.target.checked })}
              />
              {label}
            </label>
          ))}
        </fieldset>
      </div>

      <details className="ftm-signatures">
        <summary>Signataires et décision</summary>
        <div className="ftm-fields">
          <label>
            Maître d’Œuvre
            <input value={form.architect_signatory} onChange={(event) => updateField('architect_signatory', event.target.value)} />
          </label>
          <label>
            Assistant Maître d’Ouvrage
            <input value={form.assistant_signatory} onChange={(event) => updateField('assistant_signatory', event.target.value)} />
          </label>
          <label>
            Maître de l’Ouvrage
            <input value={form.owner_signatory} onChange={(event) => updateField('owner_signatory', event.target.value)} />
          </label>
          <label>
            Décision
            <select value={form.decision} onChange={(event) => updateField('decision', event.target.value as FtmDocumentData['decision'])}>
              <option value="">Non renseignée</option>
              <option value="accepted">Acceptée</option>
              <option value="refused">Refusée</option>
            </select>
          </label>
        </div>
      </details>

      {(message || error) && <p className={error ? 'error' : 'success'} role="status">{error || message}</p>}
      <div className="ftm-actions">
        <button
          type="button"
          className="primary"
          disabled={relationsBusy || scopeMissing}
          onClick={() => void runRelationAction(applyAndGenerate)}
        >
          <FileText size={17} /> {relationsBusy ? 'Génération Excel + Word…' : 'Enregistrer et générer Excel + Word'}
        </button>
        {analysis.word_download && <span>Le bouton recrée le même fichier avec les valeurs actuellement affichées.</span>}
      </div>
    </section>
  );
}
