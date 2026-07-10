import type { AnalysisSummary, EditedObjectPatch, TraceItem } from '../types';

interface ObjectInspectorProps {
  analysis: AnalysisSummary;
  pageType: string;
  selected: TraceItem | null;
  patch?: EditedObjectPatch;
  onChange: (id: string, patch: EditedObjectPatch) => void;
  onSave: () => void;
  onDelete: () => void;
}

export function ObjectInspector({ analysis, pageType, selected, patch, onChange, onSave, onDelete }: ObjectInspectorProps) {
  const rooms = Array.from(new Set([...analysis.pieces_plan, ...analysis.traceabilite.map((item) => item.room).filter(Boolean)])).sort();
  const references = analysis.catalogue_symboles
    .filter((item) => item.page_type === pageType)
    .sort((left, right) => Number(left.reference) - Number(right.reference));

  if (!selected) {
    return (
      <aside className="panel inspector inspector-empty">
        <h2>Objet sélectionné</h2>
        <p>Sélectionnez un marqueur sur le plan pour corriger sa pièce, son type ou son comptage.</p>
      </aside>
    );
  }

  const ignored = Boolean(patch?.ignored);
  const confidence = selected.source === 'manuel' ? 1 : selected.confidence ?? 0;
  const confidencePct = Math.round(confidence * 100);
  const selectedReference = patch?.reference || selected.reference;

  return (
    <aside className="panel inspector">
      <div className="inspector-object">
        <div className="ref-badge">{selected.reference}</div>
        <div>
          <h2>Objet sélectionné</h2>
          <strong>{selected.article}</strong>
          <span>{selected.categorie || selected.page_type}</span>
        </div>
      </div>

      <div className="confidence-block">
        <div>
          <span>Confiance</span>
          <strong>{confidencePct}%</strong>
        </div>
        <div className="confidence-track">
          <span style={{ width: `${confidencePct}%` }} />
        </div>
      </div>

      <dl className="object-meta">
        <dt>ID</dt>
        <dd>{selected.detection_id}</dd>
        <dt>Source</dt>
        <dd>{selected.source}</dd>
        <dt>Page</dt>
        <dd>{selected.page} · {selected.page_type}</dd>
      </dl>

      <div className="field-group">
        <label>
          Pièce
          <select value={selected.room || ''} onChange={(event) => onChange(selected.detection_id, { room: event.target.value })}>
            <option value="">Pièce non attribuée</option>
            {rooms.map((room) => <option key={room} value={room}>{room}</option>)}
          </select>
        </label>

        <label>
          Type
          <select value={selectedReference} onChange={(event) => onChange(selected.detection_id, { reference: event.target.value })}>
            {references.map((item) => (
              <option key={item.reference} value={item.reference}>Réf. {item.reference} — {item.article}</option>
            ))}
          </select>
        </label>
      </div>

      <label className="check-row inspector-check">
        <input type="checkbox" checked={ignored} onChange={(event) => onChange(selected.detection_id, { ignored: event.target.checked })} />
        Ignorer cet objet au prochain recalcul
      </label>

      <div className="inspector-actions">
        <button className="primary save-object" onClick={onSave}>Enregistrer</button>
        <button className="danger-button" onClick={onDelete}>
          {selected.displayKind === 'manual' ? 'Supprimer cet objet' : 'Retirer du comptage'}
        </button>
      </div>
      <p className="muted">Enregistrer conserve la correction. Le bouton principal recalcule et recrée l’Excel final.</p>
    </aside>
  );
}
