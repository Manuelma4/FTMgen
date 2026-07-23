import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { LogIn, RefreshCw } from 'lucide-react';
import { ApiError, getCurrentUser, redirectToLogin } from '../api';
import type { AuthUser } from '../types';

interface AuthGateProps {
  children: (user: AuthUser) => ReactNode;
}

type AuthState =
  | { status: 'checking' }
  | { status: 'authenticated'; user: AuthUser }
  | { status: 'unauthenticated' }
  | { status: 'error'; message: string };

export function AuthGate({ children }: AuthGateProps) {
  const [authState, setAuthState] = useState<AuthState>({ status: 'checking' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setAuthState({ status: 'checking' });

    void getCurrentUser(controller.signal)
      .then((user) => setAuthState({ status: 'authenticated', user }))
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        if (caught instanceof ApiError && caught.status === 401) {
          setAuthState({ status: 'unauthenticated' });
          return;
        }
        setAuthState({
          status: 'error',
          message: caught instanceof Error ? caught.message : 'Le service de connexion est indisponible.',
        });
      });

    return () => controller.abort();
  }, [attempt]);

  if (authState.status === 'authenticated') {
    return children(authState.user);
  }

  if (authState.status === 'checking') {
    return (
      <main className="auth-page">
        <section className="auth-card" role="status" aria-live="polite">
          <span className="auth-brand" aria-hidden="true">FTM</span>
          <h1>FTMgen</h1>
          <p>Vérification de votre session…</p>
          <span className="auth-progress" aria-hidden="true" />
        </section>
      </main>
    );
  }

  if (authState.status === 'unauthenticated') {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <span className="auth-brand" aria-hidden="true">FTM</span>
          <p className="auth-eyebrow">Moduo</p>
          <h1>Bienvenue sur FTMgen</h1>
          <p>Connectez-vous avec votre compte Moduo pour accéder à vos analyses et à vos documents.</p>
          <button type="button" className="primary auth-primary" onClick={redirectToLogin}>
            <LogIn size={18} aria-hidden="true" />
            Se connecter
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="auth-page">
      <section className="auth-card" role="alert">
        <span className="auth-brand" aria-hidden="true">FTM</span>
        <h1>Connexion indisponible</h1>
        <p>{authState.message}</p>
        <button type="button" className="primary auth-primary" onClick={() => setAttempt((value) => value + 1)}>
          <RefreshCw size={18} aria-hidden="true" />
          Réessayer
        </button>
      </section>
    </main>
  );
}
