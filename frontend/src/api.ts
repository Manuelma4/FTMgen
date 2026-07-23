import type { AnalysisSummary, AuthUser, Corrections, FtmDocumentData, HistoryItem, LevelOption, MarkerResponse } from './types';

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function requestJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    credentials: 'same-origin',
    ...init,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(body.detail || `HTTP ${response.status}`, response.status);
  }
  return body as T;
}

export function getCurrentUser(signal?: AbortSignal): Promise<AuthUser> {
  return requestJson<AuthUser>('/api/auth/me', { signal });
}

export function redirectToLogin(): void {
  window.location.assign('/api/auth/login');
}

export function redirectToLogout(): void {
  window.location.assign('/api/auth/logout');
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

export function recalculate(job: string, corrections: Corrections & { ftm_document?: FtmDocumentData }): Promise<AnalysisSummary> {
  return requestJson(`/api/history/${job}/corrections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corrections),
  });
}

export function generateFtmWord(job: string, data: FtmDocumentData): Promise<AnalysisSummary> {
  return requestJson(`/api/history/${job}/ftm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}
