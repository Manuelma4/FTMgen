import type { HistoryItem } from '../types';

interface HistoryPanelProps {
  items: HistoryItem[];
  onOpen: (job: string) => Promise<void>;
  onDelete: (job: string) => Promise<void>;
}

export function HistoryPanel({ items, onOpen, onDelete }: HistoryPanelProps) {
  return (
    <section className="panel history-panel">
      <h2>Historique</h2>
      <div className="history-list">
        {items.length === 0 && <p className="muted">Aucune analyse enregistrée.</p>}
        {items.map((item) => (
          <article key={item.job} className="history-item">
            <div>
              <strong>{item.niveau || 'Niveau non identifié'}</strong>
              <span>{item.symboles_detectes} objets · {item.lignes} lignes</span>
              <small>{item.excel_name}</small>
            </div>
            <div className="compact-actions">
              <button onClick={() => void onOpen(item.job)}>Ouvrir</button>
              <button
                className="danger"
                onClick={() => {
                  if (window.confirm(`Supprimer définitivement l’analyse « ${item.niveau || item.excel_name} » et ses fichiers ?`)) {
                    void onDelete(item.job);
                  }
                }}
              >
                Supprimer
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
