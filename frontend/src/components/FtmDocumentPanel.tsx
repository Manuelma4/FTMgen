import { useMemo, useState } from 'react';
import { Download, FileText, RefreshCw, Save, Trash2 } from 'lucide-react';
import { generateFtmWord } from '../api';
import { SearchableRelationField } from './SearchableRelationField';
import type { AnalysisSummary, FtmDocumentData, FtmMaterialRow } from '../types';

interface FtmDocumentPanelProps {
  analysis: AnalysisSummary;
  excelPieces: string[];
  excelMaterials: string[];
  roomMappings: Record<string, string>;
  materialMappings: Record<string, string>;
  validatedArticles: string[];
  onRoomMappingChange: (room: string, excelPiece: string) => void;
  onMaterialMappingChange: (article: string, excelMaterial: string) => void;
  onValidatedArticleChange: (article: string, checked: boolean) => void;
  onSaveCorrespondences: () => Promise<void>;
  onApplyCorrespondences: () => Promise<void>;
  onValidateArticles: (articles: string[]) => Promise<void>;
  onGenerated: (result: Pick<AnalysisSummary, 'ftm_document' | 'word_download' | 'updated_at'>) => void;
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

function suggestedMaterials(analysis: AnalysisSummary): FtmMaterialRow[] {
  const roomMappings = new Map(
    (analysis.pieces_rapprochees || []).map((item) => [normalizedValue(item.plan), item.maquette]),
  );
  const detected = new Map<string, {
    room: string;
    material: string;
    comparisonMaterial: string;
    category: string;
    quantityAfter: number;
  }>();

  for (const item of analysis.traceabilite || []) {
    if (item.ignored) continue;
    const room = String(item.room || '').trim();
    const material = String(item.article || item.original_article || item.materiel_compare || '').trim();
    const comparisonMaterial = String(item.materiel_compare || item.article || item.original_article || '').trim();
    const category = String(item.categorie || '').trim();
    if (!material) continue;
    const key = [room, material, comparisonMaterial, category].map(normalizedValue).join('|');
    const current = detected.get(key);
    if (current) {
      current.quantityAfter += 1;
    } else {
      detected.set(key, { room, material, comparisonMaterial, category, quantityAfter: 1 });
    }
  }

  return Array.from(detected.values())
    .sort((left, right) => (
      left.room.localeCompare(right.room, 'fr', { sensitivity: 'base' })
      || left.material.localeCompare(right.material, 'fr', { sensitivity: 'base' })
    ))
    .map((item, index) => {
      const mappedRoom = roomMappings.get(normalizedValue(item.room));
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
        candidatePieces.has(normalizedValue(row.piece))
        && candidateMaterials.has(normalizedValue(row.materiel))
        && (!item.category || normalizedValue(row.categorie) === normalizedValue(item.category))
      )) || analysis.comparatif.find((row) => (
        candidatePieces.has(normalizedValue(row.piece))
        && candidateMaterials.has(normalizedValue(row.materiel))
      ));

      return {
        id: `${analysis.job}-pdf-${index}`,
        room: item.room,
        material: item.material,
        comparison_room: mappedRoom || '',
        comparison_material: item.comparisonMaterial,
        quantity_before: comparison ? String(comparison.quantite_avant) : '',
        quantity_after: String(item.quantityAfter),
        unit_price: '',
        company_price: '',
      };
    });
}

function materialKeys(item: FtmMaterialRow): string[] {
  const room = normalizedValue(item.room);
  return [item.material, item.comparison_material || '']
    .map((material) => `${room}|${normalizedValue(material)}`)
    .filter((key) => !key.endsWith('|'));
}

function mergePdfMaterials(analysis: AnalysisSummary, existing: FtmMaterialRow[]): FtmMaterialRow[] {
  const existingByKey = new Map<string, FtmMaterialRow>();
  for (const item of existing || []) {
    for (const key of materialKeys(item)) existingByKey.set(key, item);
  }
  return suggestedMaterials(analysis).map((item) => {
    const previous = materialKeys(item).map((key) => existingByKey.get(key)).find(Boolean);
    return {
      ...item,
      unit_price: previous?.unit_price || '',
      company_price: previous?.company_price || '',
    };
  });
}

function defaultDocument(analysis: AnalysisSummary): FtmDocumentData {
  return {
    project_name: 'MEDIVIE 4 - HPVA',
    project_description: "Construction d'une maison médicale - Villeneuve d'Ascq - 59",
    issuer: 'MODUO',
    ftm_number: '',
    revision: '',
    subject: '',
    pole: 'Zone IRM / Local technique',
    lot: '',
    floor: analysis.niveau || '',
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
    materials_version: 2,
    materials: suggestedMaterials(analysis),
  };
}

function documentForAnalysis(analysis: AnalysisSummary): FtmDocumentData {
  const defaults = defaultDocument(analysis);
  const saved = analysis.ftm_document;
  if (!saved) return defaults;
  const document = {
    ...defaults,
    ...saved,
    categories: { ...defaults.categories, ...(saved.categories || {}) },
    attachments: { ...defaults.attachments, ...(saved.attachments || {}) },
    recipients: { ...defaults.recipients, ...(saved.recipients || {}) },
  };
  if (saved.materials_version === 2) return document;
  return {
    ...document,
    materials_version: 2,
    materials: mergePdfMaterials(analysis, saved.materials || []),
  };
}

export function FtmDocumentPanel({
  analysis,
  excelPieces,
  excelMaterials,
  roomMappings,
  materialMappings,
  validatedArticles,
  onRoomMappingChange,
  onMaterialMappingChange,
  onValidatedArticleChange,
  onSaveCorrespondences,
  onApplyCorrespondences,
  onValidateArticles,
  onGenerated,
}: FtmDocumentPanelProps) {
  const [form, setForm] = useState<FtmDocumentData>(() => documentForAnalysis(analysis));
  const [generating, setGenerating] = useState(false);
  const [relationsBusy, setRelationsBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const roomSuggestions = useMemo(() => new Map(
    (analysis.pieces_rapprochees || []).map((item) => [normalizedValue(item.plan), item.maquette]),
  ), [analysis.pieces_rapprochees]);
  const materialSuggestions = useMemo(() => new Map(
    (analysis.articles_rapproches || []).map((item) => [normalizedValue(item.plan), item.maquette]),
  ), [analysis.articles_rapproches]);

  function hasMapping(source: Record<string, string>, key: string): boolean {
    return Object.prototype.hasOwnProperty.call(source, key);
  }

  function correspondingRoom(item: FtmMaterialRow): string {
    if (hasMapping(roomMappings, item.room)) return roomMappings[item.room];
    return roomSuggestions.get(normalizedValue(item.room)) || item.comparison_room || '';
  }

  function correspondingMaterial(item: FtmMaterialRow): string {
    if (validatedArticles.includes(item.material)) return '';
    if (hasMapping(materialMappings, item.material)) return materialMappings[item.material];
    return materialSuggestions.get(normalizedValue(item.material)) || item.comparison_material || '';
  }

  function quantityBefore(item: FtmMaterialRow): string {
    const room = correspondingRoom(item);
    const material = correspondingMaterial(item);
    if (!room || !material) return '0';
    const comparison = analysis.comparatif.find((row) => (
      normalizedValue(row.piece) === normalizedValue(room)
      && normalizedValue(row.materiel) === normalizedValue(material)
    ));
    return comparison ? String(comparison.quantite_avant) : '0';
  }

  const unmatchedArticles = Array.from(new Set(
    form.materials
      .map((item) => item.material)
      .filter((article) => {
        if (!article || validatedArticles.includes(article)) return false;
        if (hasMapping(materialMappings, article)) return materialMappings[article] === '';
        return !materialSuggestions.get(normalizedValue(article));
      }),
  ));

  function updateField<Key extends keyof FtmDocumentData>(key: Key, value: FtmDocumentData[Key]): void {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateMaterialPrice(id: string, field: 'unit_price' | 'company_price', value: string): void {
    setForm((current) => ({
      ...current,
      materials: current.materials.map((item) => item.id === id ? { ...item, [field]: value } : item),
    }));
  }

  function removeMaterial(id: string): void {
    setForm((current) => ({ ...current, materials: current.materials.filter((item) => item.id !== id) }));
  }

  function restorePdfMaterials(): void {
    setForm((current) => ({
      ...current,
      materials_version: 2,
      materials: mergePdfMaterials(analysis, current.materials),
    }));
    setError('');
    setMessage('Liste rétablie exclusivement depuis les objets détectés dans le PDF.');
  }

  async function runRelationAction(action: () => Promise<void>): Promise<void> {
    setRelationsBusy(true);
    setError('');
    try {
      await action();
    } finally {
      setRelationsBusy(false);
    }
  }

  async function generate(): Promise<void> {
    setGenerating(true);
    setMessage('');
    setError('');
    try {
      const materials = form.materials.map((item) => ({
        ...item,
        comparison_room: correspondingRoom(item),
        comparison_material: correspondingMaterial(item),
        quantity_before: quantityBefore(item),
      }));
      const result = await generateFtmWord(analysis.job, { ...form, materials });
      setForm(result.ftm_document);
      onGenerated(result);
      setMessage('Word généré avec les informations visibles ci-dessous.');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Impossible de générer le Word');
    } finally {
      setGenerating(false);
    }
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
            <p>Uniquement les objets réellement détectés dans le PDF.</p>
          </div>
          <button type="button" className="compact-button" onClick={restorePdfMaterials}>
            <RefreshCw size={16} /> Rétablir depuis le PDF
          </button>
        </div>
        <div className="word-relations-toolbar">
          <div className="word-relations-copy">
            <div className="word-relations-title">
              <strong>Correspondances PDF → Excel</strong>
              <span className="word-row-count">{form.materials.length} objets PDF</span>
            </div>
            <span>Modifiez-les dans la table, puis appliquez-les pour recalculer le comparatif et l’Excel.</span>
          </div>
          <div className="actions word-relation-actions">
            <button type="button" disabled={relationsBusy} onClick={() => void runRelationAction(onSaveCorrespondences)}>
              <Save size={16} /> Enregistrer
            </button>
            <button type="button" className="primary" disabled={relationsBusy} onClick={() => void runRelationAction(onApplyCorrespondences)}>
              <RefreshCw size={16} /> Appliquer et refaire l’Excel
            </button>
            <button
              className="validate-additions-button"
              type="button"
              disabled={relationsBusy || unmatchedArticles.length === 0}
              onClick={() => void runRelationAction(() => onValidateArticles(unmatchedArticles))}
            >
              Valider {unmatchedArticles.length} ajout{unmatchedArticles.length !== 1 ? 's' : ''} sans équivalent
            </button>
          </div>
        </div>
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
                <th>Avant</th>
                <th>Après</th>
                <th>Prix unitaire</th>
                <th>Prix entreprise</th>
              </tr>
            </thead>
            <tbody>
              {form.materials.map((item) => (
                <tr key={item.id}>
                  <td className="word-source-cell">
                    <span className="word-source-value" title={item.room}>{item.room || '—'}</span>
                  </td>
                  <td className="word-source-cell">
                    <span className="word-source-value word-source-material" title={item.material}>{item.material || '—'}</span>
                  </td>
                  <td className="word-correspondence-cell">
                    <SearchableRelationField
                      value={hasMapping(roomMappings, item.room) ? roomMappings[item.room] : undefined}
                      suggested={roomSuggestions.get(normalizedValue(item.room)) || item.comparison_room || ''}
                      options={excelPieces}
                      placeholder="Rechercher une pièce Excel"
                      specialLabel="Nouvelle pièce / sans relation"
                      ariaLabel={`Pièce Excel correspondant à ${item.room}`}
                      onChange={(value) => onRoomMappingChange(item.room, value)}
                    />
                  </td>
                  <td className="word-correspondence-cell">
                    <SearchableRelationField
                      value={hasMapping(materialMappings, item.material) ? materialMappings[item.material] : undefined}
                      suggested={materialSuggestions.get(normalizedValue(item.material)) || item.comparison_material || ''}
                      options={excelMaterials}
                      disabled={validatedArticles.includes(item.material)}
                      placeholder="Rechercher un matériel Excel"
                      specialLabel={validatedArticles.includes(item.material) ? 'Validé comme ajout' : 'Sans correspondance Excel'}
                      ariaLabel={`Matériel Excel correspondant à ${item.material}`}
                      onChange={(value) => onMaterialMappingChange(item.material, value)}
                    />
                    <label className="check-row word-addition-check">
                      <input
                        type="checkbox"
                        checked={validatedArticles.includes(item.material)}
                        onChange={(event) => onValidatedArticleChange(item.material, event.target.checked)}
                      />
                      Ajout sans équivalent Excel
                    </label>
                  </td>
                  <td className="word-quantity-cell"><output className="word-quantity word-quantity-before" aria-label="Quantité avant">{quantityBefore(item)}</output></td>
                  <td className="word-quantity-cell"><output className="word-quantity word-quantity-after" aria-label="Quantité après">{item.quantity_after}</output></td>
                  <td><input aria-label="Prix unitaire" inputMode="decimal" value={item.unit_price} onChange={(event) => updateMaterialPrice(item.id, 'unit_price', event.target.value)} /></td>
                  <td><input aria-label="Prix entreprise" inputMode="decimal" value={item.company_price} onChange={(event) => updateMaterialPrice(item.id, 'company_price', event.target.value)} /></td>
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
        <button type="button" className="primary" disabled={generating} onClick={() => void generate()}>
          <FileText size={17} /> {generating ? 'Génération du Word…' : 'Enregistrer et générer le Word'}
        </button>
        {analysis.word_download && <span>Le bouton recrée le même fichier avec les valeurs actuellement affichées.</span>}
      </div>
    </section>
  );
}
