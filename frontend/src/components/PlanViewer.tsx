import { useState } from 'react';
import type { MouseEvent } from 'react';
import type { AnalysisSummary, TraceItem } from '../types';

interface PlanViewerProps {
  analysis: AnalysisSummary;
  page: number;
  pageSize: { width: number; height: number };
  mode: 'all' | 'counted' | 'review' | 'uncatalogued';
  toolMode: 'select' | 'add';
  markers: TraceItem[];
  selectedId: string | null;
  currentRef: string;
  onPageChange: (page: number) => void;
  onModeChange: (mode: 'all' | 'counted' | 'review' | 'uncatalogued') => void;
  onToolModeChange: (mode: 'select' | 'add') => void;
  onCurrentRefChange: (reference: string) => void;
  onSelect: (id: string) => void;
  onAddManual: (point: { x: number; y: number }) => void;
}

function isVisible(marker: TraceItem, mode: PlanViewerProps['mode']): boolean {
  if (mode === 'all') return true;
  if (mode === 'review') return Boolean(marker.review);
  return marker.displayKind === mode;
}

export function PlanViewer({
  analysis,
  page,
  pageSize,
  mode,
  toolMode,
  markers,
  selectedId,
  currentRef,
  onPageChange,
  onModeChange,
  onToolModeChange,
  onCurrentRefChange,
  onSelect,
  onAddManual,
}: PlanViewerProps) {
  const [zoom, setZoom] = useState(100);
  const visible = markers.filter((marker) => isVisible(marker, mode));
  const pageType = analysis.pages[String(page)] || '';
  const references = analysis.catalogue_symboles
    .filter((item) => item.page_type === pageType)
    .sort((left, right) => Number(left.reference) - Number(right.reference));

  function handleSheetClick(event: MouseEvent<HTMLDivElement>): void {
    if (toolMode !== 'add') return;
    if ((event.target as HTMLElement).closest('.pin')) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width === 0 || bounds.height === 0) return;
    onAddManual({
      x: ((event.clientX - bounds.left) / bounds.width) * pageSize.width,
      y: ((event.clientY - bounds.top) / bounds.height) * pageSize.height,
    });
  }

  function updateZoom(next: number): void {
    setZoom(Math.min(220, Math.max(55, next)));
  }

  return (
    <section className="panel plan-panel">
      <div className="viewer-toolbar">
        <label>
          Page
          <select value={page} onChange={(event) => onPageChange(Number(event.target.value))}>
            {Object.entries(analysis.pages).map(([number, type]) => (
              <option key={number} value={number}>Page {number} — {type}</option>
            ))}
          </select>
        </label>
        <div className="segmented">
          <button className={toolMode === 'select' ? 'active' : ''} onClick={() => onToolModeChange('select')}>
            Modifier
          </button>
          <button className={toolMode === 'add' ? 'active' : ''} onClick={() => onToolModeChange('add')}>
            Ajouter objet
          </button>
        </div>
        {toolMode === 'add' && (
          <label className="inline-field">
            Objet
            <select value={currentRef} onChange={(event) => onCurrentRefChange(event.target.value)}>
              {references.map((item) => (
                <option key={item.reference} value={item.reference}>
                  Réf. {item.reference} — {item.article}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="zoom-controls" aria-label="Zoom PDF">
          <button type="button" onClick={() => updateZoom(zoom - 10)}>-</button>
          <input
            type="range"
            min="55"
            max="220"
            value={zoom}
            onChange={(event) => updateZoom(Number(event.target.value))}
            aria-label="Zoom"
          />
          <button type="button" onClick={() => updateZoom(zoom + 10)}>+</button>
          <button type="button" onClick={() => updateZoom(100)}>{zoom}%</button>
        </div>
        <div className="segmented">
          {(['all', 'counted', 'review', 'uncatalogued'] as const).map((item) => (
            <button key={item} className={mode === item ? 'active' : ''} onClick={() => onModeChange(item)}>
              {item === 'all' ? 'Tous' : item === 'counted' ? 'Comptés' : item === 'review' ? 'À valider' : 'Non comptés'}
            </button>
          ))}
        </div>
      </div>

      <div className="viewer-content">
        <div
          className={`sheet ${toolMode === 'add' ? 'adding' : ''}`}
          onClick={handleSheetClick}
          style={{ width: `${zoom}%` }}
        >
          <img src={`/api/jobs/${analysis.job}/pdf/pages/${page}.png?annotated=false`} alt={`Page ${page} ${pageType}`} />
          <div className="marker-layer">
            {visible.map((marker) => {
              const left = marker.left ?? (marker.x / pageSize.width) * 100;
              const top = marker.top ?? (marker.y / pageSize.height) * 100;
              const cls = marker.review && mode === 'review' ? 'review' : marker.displayKind || 'counted';
              return (
                <button
                  key={marker.detection_id}
                  className={`pin ${cls} ${marker.ignored ? 'ignored' : ''} ${selectedId === marker.detection_id ? 'selected' : ''}`}
                  style={{ left: `${left}%`, top: `${top}%` }}
                  title={`${marker.reference} — ${marker.article}`}
                  onClick={() => onSelect(marker.detection_id)}
                >
                  {marker.reference}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="reference-strip">
        {references.map((item) => (
          <span key={`${item.page_type}-${item.reference}`} className={item.count === 0 ? 'zero' : ''}>
            <b>{item.reference}</b> {item.count}
          </span>
        ))}
      </div>
    </section>
  );
}
