import type { AnalysisSummary, Corrections, FtmDocumentData, HistoryItem, LevelOption, MarkerResponse } from './types';

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return body as T;
}

export async function inspectExcel(file: File): Promise<LevelOption[]> {
  const body = new FormData();
  body.append('excel', file);
  const data = await requestJson<{ niveaux: LevelOption[] }>('/api/excel/inspect', {
    method: 'POST',
    body,
  });
  return data.niveaux;
}

export async function runAnalysis(excel: File, pdf: File, level: string, levelName: string): Promise<AnalysisSummary> {
  const body = new FormData();
  body.append('excel', excel);
  body.append('pdf', pdf);
  body.append('niveau_excel', level);
  body.append('nom_niveau', levelName);
  return requestJson<AnalysisSummary>('/api/compare', { method: 'POST', body });
}

export async function listHistory(): Promise<HistoryItem[]> {
  const data = await requestJson<{ analyses: HistoryItem[] }>('/api/history');
  return data.analyses;
}

export function getAnalysis(job: string): Promise<AnalysisSummary> {
  return requestJson<AnalysisSummary>(`/api/history/${job}`);
}

export function deleteAnalysis(job: string): Promise<void> {
  return requestJson<void>(`/api/history/${job}`, { method: 'DELETE' });
}

export function getMarkers(job: string, page: number): Promise<MarkerResponse> {
  return requestJson<MarkerResponse>(`/api/jobs/${job}/pdf/pages/${page}/markers`);
}

export function saveDraft(job: string, corrections: Corrections): Promise<{ corrections: Corrections; updated_at: string }> {
  return requestJson(`/api/history/${job}/corrections/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corrections),
  });
}

export function recalculate(job: string, corrections: Corrections): Promise<AnalysisSummary> {
  return requestJson(`/api/history/${job}/corrections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corrections),
  });
}

export function generateFtmWord(job: string, data: FtmDocumentData): Promise<{
  ftm_document: FtmDocumentData;
  word_download: string;
  updated_at: string;
}> {
  return requestJson(`/api/history/${job}/ftm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}
