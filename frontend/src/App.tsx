import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent } from 'react';
import { FileSpreadsheet, FileText, LogOut, PanelLeftClose, PanelLeftOpen, RefreshCw, UserRound } from 'lucide-react';
import {
  deleteAnalysis,
  getAnalysis,
  getMarkers,
  inspectExcel,
  listHistory,
  recalculate,
  redirectToLogout,
  runAnalysis,
  saveDraft,
} from './api';
import { CompareTable } from './components/CompareTable';
import { FtmDocumentPanel } from './components/FtmDocumentPanel';
import { HistoryPanel } from './components/HistoryPanel';
import { ObjectInspector } from './components/ObjectInspector';
import { PlanViewer } from './components/PlanViewer';
import type {
  AnalysisSummary,
  AuthUser,
  Corrections,
  EditedObjectPatch,
  FtmDocumentData,
  HistoryItem,
  LevelOption,
  ManualObject,
  MarkerResponse,
  TraceItem,
} from './types';

interface AppProps {
  currentUser: AuthUser;
}

function emptyCorrections(): Corrections {
  return {
    rooms: [],
    manual_objects: [],
    edited_objects: {},
    room_mappings: {},
    material_mappings: {},
    validated_articles: [],
    object_relations: {},
    manual_lines: [],
    excluded_relations: [],
  };
}

function normalizeAnalysis(data: AnalysisSummary): AnalysisSummary {
  return {
    ...data,
    comparatif: data.comparatif || [],
    traceabilite: data.traceabilite || [],
    catalogue_symboles: data.catalogue_symboles || [],
    pieces_plan: data.pieces_plan || [],
    pieces_zones: data.pieces_zones || [],
    corrections: {
      ...emptyCorrections(),
      ...(data.corrections || {}),
      rooms: data.corrections?.rooms || [],
      manual_objects: data.corrections?.manual_objects || [],
      edited_objects: data.corrections?.edited_objects || {},
      room_mappings: data.corrections?.room_mappings || {},
      material_mappings: data.corrections?.material_mappings || {},
      validated_articles: data.corrections?.validated_articles || [],
      object_relations: data.corrections?.object_relations || {},
      manual_lines: data.corrections?.manual_lines || [],
      excluded_relations: data.corrections?.excluded_relations || [],
    },
    referentiel_excel: {
      ...(data.referentiel_excel || {}),
      pieces: data.referentiel_excel?.pieces || [],
      materiels: data.referentiel_excel?.materiels || [],
      piece_options: data.referentiel_excel?.piece_options || [],
      scope_options: data.referentiel_excel?.scope_options || [],
      selected_scope_id: data.referentiel_excel?.selected_scope_id || '',
      selected_scope: data.referentiel_excel?.selected_scope || null,
    },
    pieces_rapprochees: data.pieces_rapprochees || [],
    pieces_non_rapprochees: data.pieces_non_rapprochees || [],
    articles_rapproches: data.articles_rapproches || [],
    objets_composes: data.objets_composes || [],
    statuts: data.statuts || {},
    audit_excel: data.audit_excel || {},
  };
}

function uniqueSorted(values: Array<string | undefined | null>): string[] {
  return Array.from(new Set(values.map((value) => String(value || '').trim()).filter(Boolean))).sort((left, right) => (
    left.localeCompare(right, 'fr', { sensitivity: 'base' })
  ));
}

function isInvalidExcelMaterial(value: string): boolean {
  const text = String(value || '').trim().toUpperCase();
  return text.startsWith('#REF') || ['#N/A', '#VALUE!', '#NAME?', '#DIV/0!'].includes(text);
}

export function App({ currentUser }: AppProps) {
  const [excel, setExcel] = useState<File | null>(null);
  const [pdf, setPdf] = useState<File | null>(null);
  const [levels, setLevels] = useState<LevelOption[]>([]);
  const [level, setLevel] = useState('');
  const [levelName, setLevelName] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisSummary | null>(null);
  const [page, setPage] = useState(1);
  const [mode, setMode] = useState<'all' | 'counted' | 'review' | 'uncatalogued'>('counted');
  const [toolMode, setToolMode] = useState<'select' | 'add'>('select');
  const [currentRef, setCurrentRef] = useState('');
  const [markers, setMarkers] = useState<TraceItem[]>([]);
  const [pageSize, setPageSize] = useState({ width: 1, height: 1 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [manualObjects, setManualObjects] = useState<ManualObject[]>([]);
  const [editedObjects, setEditedObjects] = useState<Record<string, EditedObjectPatch>>({});
  const [roomCorrections, setRoomCorrections] = useState<Corrections['rooms']>([]);
  const [roomMappings, setRoomMappings] = useState<Record<string, string>>({});
  const [materialMappings, setMaterialMappings] = useState<Record<string, string>>({});
  const [validatedArticles, setValidatedArticles] = useState<string[]>([]);
  const [sidebarWidth, setSidebarWidth] = useState(340);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(380);
  const gridRef = useRef<HTMLElement | null>(null);
  const lastJobRef = useRef<string | null>(null);
  const referentialReloadRef = useRef<string | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const userName = currentUser.name
    || currentUser.preferred_username
    || currentUser.username
    || currentUser.email
    || currentUser.sub;
  const userIdentifier = currentUser.email && currentUser.email !== userName
    ? currentUser.email
    : currentUser.preferred_username && currentUser.preferred_username !== userName
      ? currentUser.preferred_username
      : '';

  const currentPageType = analysis?.pages[String(page)] || '';
  const selected = useMemo(
    () => markers.find((item) => item.detection_id === selectedId) || null,
    [markers, selectedId],
  );
  const corrections = useMemo<Corrections>(
    () => ({
      rooms: roomCorrections,
      manual_objects: manualObjects,
      edited_objects: editedObjects,
      room_mappings: roomMappings,
      material_mappings: materialMappings,
      validated_articles: validatedArticles,
      excel_scope_id: analysis?.corrections.excel_scope_id,
      object_relations: analysis?.corrections.object_relations || {},
      manual_lines: analysis?.corrections.manual_lines || [],
      excluded_relations: analysis?.corrections.excluded_relations || [],
    }),
    [roomCorrections, manualObjects, editedObjects, roomMappings, materialMappings, validatedArticles, analysis?.corrections],
  );
  const selectedPatch = selected
    ? selected.displayKind === 'manual'
      ? { room: selected.room, reference: selected.reference, ignored: selected.ignored }
      : editedObjects[selected.detection_id]
    : undefined;
  const excelPieces = useMemo(() => {
    if (!analysis) return [];
    const detailed = analysis.referentiel_excel?.piece_options || [];
    if (detailed.length > 0) return detailed;
    return uniqueSorted(analysis.referentiel_excel?.pieces || []).map((piece) => ({
      id: piece,
      label: piece,
      niveau: analysis.niveau_excel_selectionne || analysis.niveau || '',
      occupation: '',
      piece,
      numero: '',
    }));
  }, [analysis]);
  const excelMaterials = useMemo(() => {
    if (!analysis) return [];
    const referential = uniqueSorted(analysis.referentiel_excel?.materiels || [])
      .filter((material) => !isInvalidExcelMaterial(material));
    if (referential.length > 0) return referential;
    // Compatibilité avec une très ancienne analyse sans référentiel embarqué.
    // Les propositions restent alors limitées aux vrais matériels « avant ».
    return uniqueSorted(
      analysis.comparatif
        .filter((row) => row.quantite_avant > 0)
        .map((row) => row.materiel),
    ).filter((material) => !isInvalidExcelMaterial(material));
  }, [analysis]);
  useEffect(() => {
    void refreshHistory();
  }, []);

  useEffect(() => {
    if (!analysis) {
      lastJobRef.current = null;
      return;
    }
    // Ne réinitialiser la page et la sélection que lorsqu'on ouvre une autre
    // analyse : une simple sauvegarde de brouillon ne doit pas faire perdre le contexte.
    const isNewJob = analysis.job !== lastJobRef.current;
    lastJobRef.current = analysis.job;
    setRoomCorrections(analysis.corrections.rooms || []);
    setManualObjects(analysis.corrections.manual_objects || []);
    setEditedObjects(analysis.corrections.edited_objects || {});
    setRoomMappings(analysis.corrections.room_mappings || {});
    setMaterialMappings(analysis.corrections.material_mappings || {});
    setValidatedArticles(analysis.corrections.validated_articles || []);
    if (isNewJob) {
      const pages = Object.keys(analysis.pages || {});
      setPage(Number(pages.find((item) => analysis.pages[item] === 'ELECTRICITE') || pages[0] || 1));
      setSelectedId(null);
    }
  }, [analysis]);

  useEffect(() => {
    if (!analysis) return;
    void loadMarkers(analysis.job, page);
  }, [analysis, page, manualObjects]);

  useEffect(() => {
    if (!analysis) return;
    const references = analysis.catalogue_symboles.filter((item) => item.page_type === currentPageType);
    if (references.length > 0 && !references.some((item) => String(item.reference) === String(currentRef))) {
      setCurrentRef(String(references[0].reference));
    }
  }, [analysis, currentPageType, currentRef]);

  useEffect(() => {
    if (!analysis?.job) return;
    const hasReferential = Boolean(
      analysis.referentiel_excel?.pieces?.length || analysis.referentiel_excel?.materiels?.length,
    );
    if (hasReferential || referentialReloadRef.current === analysis.job) return;
    referentialReloadRef.current = analysis.job;
    void getAnalysis(analysis.job)
      .then((data) => {
        setAnalysis((current) => (
          current?.job === data.job ? normalizeAnalysis(data) : current
        ));
      })
      .catch(() => undefined);
  }, [analysis?.job, analysis?.referentiel_excel?.pieces?.length, analysis?.referentiel_excel?.materiels?.length]);

  function buildCorrections(overrides: Partial<Corrections> = {}): Corrections {
    return { ...corrections, ...overrides };
  }

  async function saveCorrectionSet(nextCorrections: Corrections, successMessage: string): Promise<void> {
    if (!analysis) return;
    setStatus('Enregistrement des corrections...');
    setError('');
    try {
      const saved = await saveDraft(analysis.job, nextCorrections);
      setAnalysis((current) => (current ? { ...current, corrections: saved.corrections, updated_at: saved.updated_at } : current));
      setStatus(successMessage);
      await refreshHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Impossible d’enregistrer les corrections');
      setStatus('');
    }
  }

  async function recalculateWithCorrections(nextCorrections: Corrections, ftmDocument?: FtmDocumentData): Promise<AnalysisSummary | null> {
    if (!analysis) return null;
    setError('');
    setStatus('Recalcul du comparatif et génération de l’Excel...');
    try {
      const result = normalizeAnalysis(await recalculate(analysis.job, {
        ...nextCorrections,
        ...(ftmDocument ? { ftm_document: ftmDocument } : {}),
      }));
      setAnalysis(result);
      await refreshHistory();
      return result;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Échec du recalcul');
      return null;
    } finally {
      setStatus('');
    }
  }

  async function refreshHistory(): Promise<void> {
    setHistory(await listHistory());
  }

  async function handleExcel(file: File): Promise<void> {
    setExcel(file);
    setError('');
    try {
      const detected = await inspectExcel(file);
      setLevels(detected);
      const last = detected[detected.length - 1];
      if (last) {
        setLevel(last.value);
        setLevelName(last.value);
      }
    } catch (caught) {
      setLevels([]);
      setLevel('');
      setLevelName('');
      setError(caught instanceof Error ? caught.message : 'Excel illisible');
    }
  }

  async function handleRun(): Promise<void> {
    if (!excel || !pdf || !level) return;
    setStatus('Analyse en cours...');
    setError('');
    try {
      const result = normalizeAnalysis(await runAnalysis(excel, pdf, level, levelName));
      setAnalysis(result);
      await refreshHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Erreur inconnue');
    } finally {
      setStatus('');
    }
  }

  async function openHistory(job: string): Promise<void> {
    setStatus('Ouverture de l’analyse...');
    setError('');
    try {
      setAnalysis(normalizeAnalysis(await getAnalysis(job)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Analyse introuvable');
    } finally {
      setStatus('');
    }
  }

  async function removeHistory(job: string): Promise<void> {
    setError('');
    try {
      await deleteAnalysis(job);
      if (analysis?.job === job) setAnalysis(null);
      await refreshHistory();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Impossible de supprimer l’analyse');
    }
  }

  async function loadMarkers(job: string, pageNumber: number): Promise<void> {
    const response: MarkerResponse = await getMarkers(job, pageNumber);
    setPageSize({ width: response.width || 1, height: response.height || 1 });
    // Ré-appliquer les corrections en attente : sans cela, un changement de page
    // faisait revenir les marqueurs à leurs valeurs d'origine côté serveur.
    const catalogue = analysis?.catalogue_symboles || [];
    const auto = response.counted.map((item) => {
      const patch = editedObjects[item.detection_id] || {};
      const meta = patch.reference
        ? catalogue.find((entry) => (
          entry.page_type === item.page_type && String(entry.reference) === String(patch.reference)
        ))
        : null;
      return {
        ...item,
        ...patch,
        ...(meta ? {
          marker: String(meta.reference),
          reference: String(meta.reference),
          article: meta.article,
          categorie: meta.categorie,
        } : {}),
        ignored: Boolean(patch.ignored),
        displayKind: 'counted' as const,
        review: Boolean(item.needs_review || item.statut?.startsWith('À')),
      };
    });
    const uncatalogued = response.uncatalogued.map((item) => ({
      ...item,
      displayKind: 'uncatalogued' as const,
      review: false,
    }));
    const manual = manualObjects
      .filter((item) => item.page === pageNumber)
      .map((item) => ({
        ...item,
        detection_id: item.id,
        source: 'manuel',
        page_type: item.page_type,
        marker: item.reference,
        reference: item.reference,
        label: item.label || 'Ajout manuel',
        article: item.article || '',
        categorie: item.categorie || '',
        confidence: 1,
        room: item.room || '',
        ignored: Boolean(item.ignored),
        room_dist: 0,
        left: (item.x / (response.width || 1)) * 100,
        top: (item.y / (response.height || 1)) * 100,
        displayKind: 'manual' as const,
        review: false,
      }));
    setMarkers([...auto, ...uncatalogued, ...manual]);
  }

  function updateSelected(id: string, patch: EditedObjectPatch): void {
    const marker = markers.find((item) => item.detection_id === id);
    if (!marker) return;
    const meta = patch.reference && analysis
      ? analysis.catalogue_symboles.find((item) => (
        item.page_type === marker.page_type && String(item.reference) === String(patch.reference)
      ))
      : null;
    const normalizedPatch = meta ? { ...patch, reference: String(meta.reference) } : patch;
    const visualPatch = meta
      ? { ...normalizedPatch, marker: String(meta.reference), article: meta.article, categorie: meta.categorie }
      : normalizedPatch;
    if (marker.displayKind === 'manual') {
      setManualObjects((items) => items.map((item) => (
        item.id === id
          ? {
            ...item,
            ...normalizedPatch,
            ...(meta ? { article: meta.article, categorie: meta.categorie } : {}),
          }
          : item
      )));
    } else {
      setEditedObjects((items) => ({ ...items, [id]: { ...(items[id] || {}), ...normalizedPatch } }));
    }
    setMarkers((items) => items.map((item) => (item.detection_id === id ? { ...item, ...visualPatch } : item)));
  }

  function addManualObject(point: { x: number; y: number }): void {
    if (!analysis || !currentPageType || !currentRef) return;
    const meta = analysis.catalogue_symboles.find((item) => (
      item.page_type === currentPageType && String(item.reference) === String(currentRef)
    ));
    if (!meta) return;
    const item: ManualObject = {
      id: `m-${Date.now()}`,
      page,
      page_type: currentPageType,
      reference: String(meta.reference),
      article: meta.article,
      categorie: meta.categorie,
      label: 'Ajout manuel',
      x: Math.round(point.x * 10) / 10,
      y: Math.round(point.y * 10) / 10,
      room: '',
    };
    setManualObjects((items) => [...items, item]);
    setSelectedId(item.id);
    setMode('all');
    setToolMode('select');
    setStatus('Objet ajouté. Enregistrer conserve la correction avant le recalcul.');
  }

  async function saveSelectedDraft(): Promise<void> {
    if (!analysis) return;
    await saveCorrectionSet(corrections, 'Correction enregistrée. Recalculez quand vous voulez refaire l’Excel.');
  }

  async function deleteSelectedObject(): Promise<void> {
    if (!analysis || !selected) return;
    setError('');
    setStatus('Suppression de l’objet...');
    try {
      let nextManual = manualObjects;
      let nextEdited = editedObjects;
      if (selected.displayKind === 'manual') {
        nextManual = manualObjects.filter((item) => item.id !== selected.detection_id);
        setManualObjects(nextManual);
        setMarkers((items) => items.filter((item) => item.detection_id !== selected.detection_id));
        setSelectedId(null);
      } else {
        // Une détection automatique n'est pas retirée de l'affichage : elle est
        // marquée ignorée (atténuée) et reste sélectionnable pour annuler.
        nextEdited = { ...editedObjects, [selected.detection_id]: { ...(editedObjects[selected.detection_id] || {}), ignored: true } };
        setEditedObjects(nextEdited);
        setMarkers((items) => items.map((item) => (
          item.detection_id === selected.detection_id ? { ...item, ignored: true } : item
        )));
      }
      await saveCorrectionSet(
        buildCorrections({ manual_objects: nextManual, edited_objects: nextEdited }),
        selected.displayKind === 'manual'
          ? 'Objet supprimé. Le prochain recalcul mettra l’Excel à jour.'
          : 'Objet ignoré : il sera retiré au prochain recalcul. Décochez « Ignorer » pour annuler.',
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Impossible de supprimer l’objet');
      setStatus('');
    }
  }

  async function applyFtmDocument(document: FtmDocumentData): Promise<AnalysisSummary | null> {
    return recalculateWithCorrections({
      ...corrections,
      excel_scope_id: document.excel_scope_id || corrections.excel_scope_id,
    }, document);
  }

  const canRun = Boolean(excel && pdf && level);
  const workspaceStyle = {
    '--sidebar-width': sidebarCollapsed ? '64px' : `${sidebarWidth}px`,
  } as CSSProperties;
  const gridStyle = { '--inspector-width': `${inspectorWidth}px` } as CSSProperties;

  function startSidebarResize(event: PointerEvent<HTMLDivElement>): void {
    if (sidebarCollapsed) return;
    event.currentTarget.setPointerCapture(event.pointerId);

    function handleMove(moveEvent: globalThis.PointerEvent): void {
      setSidebarWidth(Math.min(520, Math.max(280, moveEvent.clientX - 14)));
    }

    function stopResize(): void {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', stopResize);
    }

    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', stopResize, { once: true });
  }

  function startResize(event: PointerEvent<HTMLDivElement>): void {
    event.currentTarget.setPointerCapture(event.pointerId);
    const initialRect = gridRef.current?.getBoundingClientRect();
    if (!initialRect) return;
    const gridRight = initialRect.right;

    function handleMove(moveEvent: globalThis.PointerEvent): void {
      const next = gridRight - moveEvent.clientX;
      setInspectorWidth(Math.min(560, Math.max(300, next)));
    }

    function stopResize(): void {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', stopResize);
    }

    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', stopResize, { once: true });
  }

  return (
    <main>
      <header className="app-header">
        <div>
          <h1>FTMgen</h1>
          <p>Comparatif PDF / Excel avec corrections traçables</p>
        </div>
        <div className="app-header-actions">
          <div className="app-user">
            <UserRound size={20} aria-hidden="true" />
            <span>
              <span className="sr-only">Utilisateur connecté :</span>
              <strong>{userName}</strong>
              {userIdentifier && <small>{userIdentifier}</small>}
            </span>
          </div>
          <button type="button" className="ghost" onClick={() => void refreshHistory()}>
            <RefreshCw size={16} aria-hidden="true" /> Actualiser
          </button>
          <button type="button" className="ghost" onClick={redirectToLogout}>
            <LogOut size={16} aria-hidden="true" /> Se déconnecter
          </button>
        </div>
      </header>

      <section className={`workspace ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`} style={workspaceStyle}>
        <aside className="left-rail">
          <div className="sidebar-top">
            {!sidebarCollapsed && <strong>Projet</strong>}
            <button
              type="button"
              className="icon-button"
              onClick={() => setSidebarCollapsed((value) => !value)}
              aria-label={sidebarCollapsed ? 'Ouvrir la barre latérale' : 'Fermer la barre latérale'}
            >
              {sidebarCollapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
          </div>
          {!sidebarCollapsed && (
          <>
          <section className="panel">
            <h2>Nouvelle analyse</h2>
            <label className="file-box">
              <FileSpreadsheet size={24} />
              <span>{excel?.name || 'Choisir Excel'}</span>
              <input type="file" accept=".xlsx,.xlsm" onChange={(event) => event.target.files?.[0] && void handleExcel(event.target.files[0])} />
            </label>
            <label className="file-box">
              <FileText size={24} />
              <span>{pdf?.name || 'Choisir PDF'}</span>
              <input type="file" accept=".pdf" onChange={(event) => event.target.files?.[0] && setPdf(event.target.files[0])} />
            </label>
            <label>
              Niveau / feuille Excel
              <select value={level} onChange={(event) => setLevel(event.target.value)}>
                <option value="">Aucun niveau</option>
                {levels.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.value} ({item.pieces} pièces)
                  </option>
                ))}
              </select>
            </label>
            <label>
              Nom affiché
              <input value={levelName} onChange={(event) => setLevelName(event.target.value)} />
            </label>
            <button className="primary" disabled={!canRun || status !== ''} onClick={() => void handleRun()}>
              Lancer l’analyse
            </button>
          </section>

          <HistoryPanel items={history} onOpen={openHistory} onDelete={removeHistory} />
          </>
          )}
        </aside>
        <div className="sidebar-resizer" onPointerDown={startSidebarResize} />

        <section className="main-pane">
          {(error || status) && (
            <section className="panel feedback" role="status">
              {error ? <p className="error">{error}</p> : <p className="status">{status}</p>}
            </section>
          )}
          {analysis ? (
            <>
              <section className="summary-bar">
                <div>
                  <h2>{analysis.niveau || 'Niveau non identifié'}</h2>
                  <p>{analysis.symboles_detectes} objets · {analysis.lignes} lignes comparées</p>
                </div>
                <div className="actions">
                  {analysis.download && <a className="button" href={analysis.download}>Télécharger Excel</a>}
                  {analysis.word_download && <a className="button" href={analysis.word_download}>Télécharger Word</a>}
                  {analysis.pdf_original && <a className="button" href={analysis.pdf_original} target="_blank" rel="noreferrer">PDF original</a>}
                </div>
              </section>

              <FtmDocumentPanel
                key={analysis.job}
                analysis={analysis}
                excelPieces={excelPieces}
                excelMaterials={excelMaterials}
                onApplyDocument={applyFtmDocument}
              />

              <section className="analysis-grid" ref={gridRef} style={gridStyle}>
                <PlanViewer
                  analysis={analysis}
                  page={page}
                  pageSize={pageSize}
                  mode={mode}
                  toolMode={toolMode}
                  markers={markers}
                  selectedId={selectedId}
                  currentRef={currentRef}
                  onModeChange={setMode}
                  onToolModeChange={setToolMode}
                  onPageChange={setPage}
                  onCurrentRefChange={setCurrentRef}
                  onSelect={setSelectedId}
                  onAddManual={addManualObject}
                />
                <div
                  className="splitter"
                  role="separator"
                  aria-label="Redimensionner le panneau objet"
                  onPointerDown={startResize}
                />
                <ObjectInspector
                  analysis={analysis}
                  pageType={currentPageType}
                  selected={selected}
                  patch={selectedPatch}
                  onChange={updateSelected}
                  onSave={() => void saveSelectedDraft()}
                  onDelete={() => void deleteSelectedObject()}
                />
              </section>

              <CompareTable rows={analysis.comparatif} />
            </>
          ) : (
            <section className="empty-state">
              <h2>Aucune analyse ouverte</h2>
              <p>Chargez un Excel et un PDF, ou ouvrez une analyse depuis l’historique.</p>
            </section>
          )}
        </section>
      </section>
    </main>
  );
}
